# -*- coding: utf-8 -*-
"""
===================================
A股自选股智能分析系统 - 主调度程序
===================================

职责：
1. 协调各模块完成股票分析流程
2. 实现低并发的线程池调度
3. 全局异常处理，确保单股失败不影响整体
4. 提供命令行入口

使用方式：
    python main.py              # 正常运行
    python main.py --debug      # 调试模式
    python main.py --dry-run    # 仅获取数据不分析

交易理念（已融入分析）：
- 严进策略：不追高，乖离率 > 5% 不买入
- 趋势交易：只做 MA5>MA10>MA20 多头排列
- 效率优先：关注筹码集中度好的股票
- 买点偏好：缩量回踩 MA5/MA10 支撑
"""
import os

# 代理配置 - 仅在本地环境使用，GitHub Actions 不需要
# 优先从环境变量读取，如果没有配置则跳过（不强制使用代理）
if os.getenv("GITHUB_ACTIONS") != "true":
    # 从环境变量读取代理配置，如果未设置则不使用代理
    http_proxy = os.getenv("HTTP_PROXY") or os.getenv("http_proxy")
    https_proxy = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")
    
    if http_proxy:
        os.environ["http_proxy"] = http_proxy
    if https_proxy:
        os.environ["https_proxy"] = https_proxy
    # 如果都没有配置，则不设置代理，直接连接（适用于国内网络环境）

import argparse
import csv
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

from config import get_config, Config
from storage import get_db, DatabaseManager
from data_provider import DataFetcherManager
from data_provider.akshare_fetcher import AkshareFetcher, RealtimeQuote, ChipDistribution
from analyzer import GeminiAnalyzer, AnalysisResult, STOCK_NAME_MAP
from notification import NotificationService, send_daily_report
from search_service import SearchService, SearchResponse
from stock_analyzer import StockTrendAnalyzer, TrendAnalysisResult
from market_analyzer import MarketAnalyzer

# 配置日志格式
LOG_FORMAT = '%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s'
LOG_DATE_FORMAT = '%Y-%m-%d %H:%M:%S'


def setup_logging(debug: bool = False, log_dir: str = "./logs") -> None:
    """
    配置日志系统（同时输出到控制台和文件）
    
    Args:
        debug: 是否启用调试模式
        log_dir: 日志文件目录
    """
    level = logging.DEBUG if debug else logging.INFO
    
    # 创建日志目录
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    
    # 日志文件路径（按日期分文件）
    today_str = datetime.now().strftime('%Y%m%d')
    log_file = log_path / f"stock_analysis_{today_str}.log"
    debug_log_file = log_path / f"stock_analysis_debug_{today_str}.log"
    
    # 创建根 logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)  # 根 logger 设为 DEBUG，由 handler 控制输出级别
    
    # Handler 1: 控制台输出
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))
    root_logger.addHandler(console_handler)
    
    # Handler 2: 常规日志文件（INFO 级别，10MB 轮转）
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))
    root_logger.addHandler(file_handler)
    
    # Handler 3: 调试日志文件（DEBUG 级别，包含所有详细信息）
    debug_handler = RotatingFileHandler(
        debug_log_file,
        maxBytes=50 * 1024 * 1024,  # 50MB
        backupCount=3,
        encoding='utf-8'
    )
    debug_handler.setLevel(logging.DEBUG)
    debug_handler.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))
    root_logger.addHandler(debug_handler)
    
    # 降低第三方库的日志级别
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('sqlalchemy').setLevel(logging.WARNING)
    logging.getLogger('google').setLevel(logging.WARNING)
    logging.getLogger('httpx').setLevel(logging.WARNING)
    
    logging.info(f"日志系统初始化完成，日志目录: {log_path.absolute()}")
    logging.info(f"常规日志: {log_file}")
    logging.info(f"调试日志: {debug_log_file}")


logger = logging.getLogger(__name__)


class StockAnalysisPipeline:
    """
    股票分析主流程调度器
    
    职责：
    1. 管理整个分析流程
    2. 协调数据获取、存储、搜索、分析、通知等模块
    3. 实现并发控制和异常处理
    """
    
    def __init__(
        self,
        config: Optional[Config] = None,
        max_workers: Optional[int] = None
    ):
        """
        初始化调度器
        
        Args:
            config: 配置对象（可选，默认使用全局配置）
            max_workers: 最大并发线程数（可选，默认从配置读取）
        """
        self.config = config or get_config()
        self.max_workers = max_workers or self.config.max_workers
        
        # 初始化各模块
        self.db = get_db()
        self.fetcher_manager = DataFetcherManager()
        self.akshare_fetcher = AkshareFetcher()  # 用于获取增强数据（量比、筹码等）
        self.trend_analyzer = StockTrendAnalyzer()  # 趋势分析器
        self.analyzer = GeminiAnalyzer()
        self.notifier = NotificationService()
        
        # 初始化搜索服务
        self.search_service = SearchService(
            tavily_keys=self.config.tavily_api_keys,
            serpapi_keys=self.config.serpapi_keys,
        )
        
        logger.info(f"调度器初始化完成，最大并发数: {self.max_workers}")
        logger.info("已启用趋势分析器 (MA5>MA10>MA20 多头判断)")
        if self.search_service.is_available:
            logger.info("搜索服务已启用 (Tavily/SerpAPI)")
        else:
            logger.warning("搜索服务未启用（未配置 API Key）")
    
    def fetch_and_save_stock_data(
        self, 
        code: str,
        force_refresh: bool = False
    ) -> Tuple[bool, Optional[str]]:
        """
        获取并保存单只股票数据
        
        断点续传逻辑：
        1. 检查数据库是否已有今日数据
        2. 如果有且不强制刷新，则跳过网络请求
        3. 否则从数据源获取并保存
        
        Args:
            code: 股票代码
            force_refresh: 是否强制刷新（忽略本地缓存）
            
        Returns:
            Tuple[是否成功, 错误信息]
        """
        try:
            today = date.today()
            
            # 断点续传检查：如果今日数据已存在，跳过
            if not force_refresh and self.db.has_today_data(code, today):
                logger.info(f"[{code}] 今日数据已存在，跳过获取（断点续传）")
                return True, None
            
            # 从数据源获取数据
            logger.info(f"[{code}] 开始从数据源获取数据...")
            df, source_name = self.fetcher_manager.get_daily_data(code, days=self.config.history_days)
            
            if df is None or df.empty:
                return False, "获取数据为空"
            
            # 保存到数据库
            saved_count = self.db.save_daily_data(df, code, source_name)
            logger.info(f"[{code}] 数据保存成功（来源: {source_name}，新增 {saved_count} 条）")
            
            return True, None
            
        except Exception as e:
            error_msg = f"获取/保存数据失败: {str(e)}"
            logger.error(f"[{code}] {error_msg}")
            return False, error_msg
    
    def analyze_stock(self, code: str) -> Optional[AnalysisResult]:
        """
        分析单只股票（增强版：含量比、换手率、筹码分析、多维度情报）
        
        流程：
        1. 获取实时行情（量比、换手率）
        2. 获取筹码分布
        3. 进行趋势分析（基于交易理念）
        4. 多维度情报搜索（最新消息+风险排查+业绩预期）
        5. 从数据库获取分析上下文
        6. 调用 AI 进行综合分析
        
        Args:
            code: 股票代码
            
        Returns:
            AnalysisResult 或 None（如果分析失败）
        """
        try:
            # 获取股票名称（优先从实时行情获取真实名称）
            stock_name = STOCK_NAME_MAP.get(code, '')
            
            # Step 1: 获取实时行情（量比、换手率等）
            realtime_quote: Optional[RealtimeQuote] = None
            try:
                realtime_quote = self.akshare_fetcher.get_realtime_quote(code)
                if realtime_quote:
                    # 使用实时行情返回的真实股票名称
                    if realtime_quote.name:
                        stock_name = realtime_quote.name
                    logger.info(f"[{code}] {stock_name} 实时行情: 价格={realtime_quote.price}, "
                              f"量比={realtime_quote.volume_ratio}, 换手率={realtime_quote.turnover_rate}%")
            except Exception as e:
                logger.warning(f"[{code}] 获取实时行情失败: {e}")
            
            # 如果还是没有名称，使用代码作为名称
            if not stock_name:
                stock_name = f'股票{code}'
            
            # Step 2: 获取筹码分布
            chip_data: Optional[ChipDistribution] = None
            try:
                chip_data = self.akshare_fetcher.get_chip_distribution(code)
                if chip_data:
                    logger.info(f"[{code}] 筹码分布: 获利比例={chip_data.profit_ratio:.1%}, "
                              f"90%集中度={chip_data.concentration_90:.2%}")
            except Exception as e:
                logger.warning(f"[{code}] 获取筹码分布失败: {e}")
            
            # Step 2.1: 获取资金流向
            fund_flow: Optional[Dict[str, Any]] = None
            try:
                fund_flow = self.akshare_fetcher.get_fund_flow(code)
                if fund_flow:
                    logger.info(f"[{code}] 资金流向: 主力净流入={fund_flow.get('main_net_inflow', 0):.2f}")
            except Exception as e:
                logger.warning(f"[{code}] 获取资金流向失败: {e}")
            
            # Step 3: 趋势分析（基于交易理念）
            trend_result: Optional[TrendAnalysisResult] = None
            try:
                # 获取历史数据进行趋势分析
                context = self.db.get_analysis_context(code)
                if context and 'raw_data' in context:
                    import pandas as pd
                    raw_data = context['raw_data']
                    if isinstance(raw_data, list) and len(raw_data) > 0:
                        df = pd.DataFrame(raw_data)
                        trend_result = self.trend_analyzer.analyze(df, code)
                        logger.info(f"[{code}] 趋势分析: {trend_result.trend_status.value}, "
                                  f"买入信号={trend_result.buy_signal.value}, 评分={trend_result.signal_score}")
            except Exception as e:
                logger.warning(f"[{code}] 趋势分析失败: {e}")
            
            # Step 4: 多维度情报搜索（最新消息+风险排查+业绩预期）
            news_context = None
            if self.search_service.is_available:
                logger.info(f"[{code}] 开始多维度情报搜索...")
                
                # 使用多维度搜索（最多3次搜索）
                intel_results = self.search_service.search_comprehensive_intel(
                    stock_code=code,
                    stock_name=stock_name,
                    max_searches=3
                )
                
                # 格式化情报报告
                if intel_results:
                    news_context = self.search_service.format_intel_report(intel_results, stock_name)
                    total_results = sum(
                        len(r.results) for r in intel_results.values() if r.success
                    )
                    logger.info(f"[{code}] 情报搜索完成: 共 {total_results} 条结果")
                    logger.debug(f"[{code}] 情报搜索结果:\n{news_context}")
            else:
                logger.info(f"[{code}] 搜索服务不可用，跳过情报搜索")
            
            # Step 5: 获取分析上下文（技术面数据）
            context = self.db.get_analysis_context(code)
            
            if context is None:
                logger.warning(f"[{code}] 无法获取分析上下文，跳过分析")
                return None
            
            # Step 6: 增强上下文数据（添加实时行情、筹码、趋势分析结果、股票名称）
            enhanced_context = self._enhance_context(
                context, 
                realtime_quote, 
                chip_data, 
                trend_result,
                fund_flow,
                stock_name  # 传入股票名称
            )
            
            # Step 7: 调用 AI 分析（传入增强的上下文和新闻）
            result = self.analyzer.analyze(enhanced_context, news_context=news_context)
            
            return result
            
        except Exception as e:
            logger.error(f"[{code}] 分析失败: {e}")
            logger.exception(f"[{code}] 详细错误信息:")
            return None
    
    def _enhance_context(
        self,
        context: Dict[str, Any],
        realtime_quote: Optional[RealtimeQuote],
        chip_data: Optional[ChipDistribution],
        trend_result: Optional[TrendAnalysisResult],
        fund_flow: Optional[Dict[str, Any]],
        stock_name: str = ""
    ) -> Dict[str, Any]:
        """
        增强分析上下文
        
        将实时行情、筹码分布、资金流向、趋势分析结果、股票名称添加到上下文中
        
        Args:
            context: 原始上下文
            realtime_quote: 实时行情数据
            chip_data: 筹码分布数据
            trend_result: 趋势分析结果
            fund_flow: 资金流向数据
            stock_name: 股票名称
            
        Returns:
            增强后的上下文
        """
        enhanced = context.copy()
        
        # 添加股票名称
        if stock_name:
            enhanced['stock_name'] = stock_name
        elif realtime_quote and realtime_quote.name:
            enhanced['stock_name'] = realtime_quote.name
        
        # 添加实时行情
        if realtime_quote:
            enhanced['realtime'] = {
                'name': realtime_quote.name,  # 股票名称
                'price': realtime_quote.price,
                'volume_ratio': realtime_quote.volume_ratio,
                'volume_ratio_desc': self._describe_volume_ratio(realtime_quote.volume_ratio),
                'turnover_rate': realtime_quote.turnover_rate,
                'pe_ratio': realtime_quote.pe_ratio,
                'pb_ratio': realtime_quote.pb_ratio,
                'total_mv': realtime_quote.total_mv,
                'circ_mv': realtime_quote.circ_mv,
                'change_60d': realtime_quote.change_60d,
            }
        
        # 添加筹码分布
        if chip_data:
            current_price = realtime_quote.price if realtime_quote else 0
            enhanced['chip'] = {
                'profit_ratio': chip_data.profit_ratio,
                'avg_cost': chip_data.avg_cost,
                'concentration_90': chip_data.concentration_90,
                'concentration_70': chip_data.concentration_70,
                'chip_status': chip_data.get_chip_status(current_price),
            }
        
        # 添加趋势分析结果
        if trend_result:
            enhanced['trend_analysis'] = {
                'trend_status': trend_result.trend_status.value,
                'ma_alignment': trend_result.ma_alignment,
                'trend_strength': trend_result.trend_strength,
                'bias_ma5': trend_result.bias_ma5,
                'bias_ma10': trend_result.bias_ma10,
                'volume_status': trend_result.volume_status.value,
                'volume_trend': trend_result.volume_trend,
                'buy_signal': trend_result.buy_signal.value,
                'signal_score': trend_result.signal_score,
                'signal_reasons': trend_result.signal_reasons,
                'risk_factors': trend_result.risk_factors,
                'atr': trend_result.atr,
            }
            
            # 风险管理建议（基于成本价与总资金）
            position = context.get('position')
            capital = context.get('capital')
            cost_price = position.get('cost_price') if position else None
            stop_loss = self.trend_analyzer.calculate_stop_loss(trend_result, cost_price)
            
            risk_management = {
                'stop_loss': stop_loss,
            }
            
            if capital and capital.get('total_capital'):
                entry_price = (
                    realtime_quote.price if realtime_quote and realtime_quote.price
                    else trend_result.current_price
                )
                stop_price = stop_loss.get('cost_atr_stop') if cost_price else stop_loss.get('ma20_stop')
                if stop_price and entry_price:
                    risk_management['position_sizing'] = self.trend_analyzer.calculate_position_size(
                        total_capital=capital['total_capital'],
                        entry_price=entry_price,
                        stop_loss=stop_price,
                        risk_per_trade=capital.get('risk_per_trade', 0.02)
                    )
                    risk_management['capital'] = capital
            
            enhanced['risk_management'] = risk_management

        # 添加资金流向
        if fund_flow:
            enhanced['fund_flow'] = fund_flow
        
        return enhanced
    
    def _describe_volume_ratio(self, volume_ratio: float) -> str:
        """
        量比描述
        
        量比 = 当前成交量 / 过去5日平均成交量
        """
        if volume_ratio < 0.5:
            return "极度萎缩"
        elif volume_ratio < 0.8:
            return "明显萎缩"
        elif volume_ratio < 1.2:
            return "正常"
        elif volume_ratio < 2.0:
            return "温和放量"
        elif volume_ratio < 3.0:
            return "明显放量"
        else:
            return "巨量"
    
    def process_single_stock(
        self, 
        code: str,
        skip_analysis: bool = False
    ) -> Optional[AnalysisResult]:
        """
        处理单只股票的完整流程
        
        包括：
        1. 获取数据
        2. 保存数据
        3. AI 分析
        
        此方法会被线程池调用，需要处理好异常
        
        Args:
            code: 股票代码
            skip_analysis: 是否跳过 AI 分析
            
        Returns:
            AnalysisResult 或 None
        """
        logger.info(f"========== 开始处理 {code} ==========")
        
        try:
            # Step 1: 获取并保存数据
            success, error = self.fetch_and_save_stock_data(code)
            
            if not success:
                logger.warning(f"[{code}] 数据获取失败: {error}")
                # 即使获取失败，也尝试用已有数据分析
            
            # Step 2: AI 分析
            if skip_analysis:
                logger.info(f"[{code}] 跳过 AI 分析（dry-run 模式）")
                return None
            
            result = self.analyze_stock(code)
            
            if result:
                logger.info(
                    f"[{code}] 分析完成: {result.operation_advice}, "
                    f"评分 {result.sentiment_score}"
                )
            
            return result
            
        except Exception as e:
            # 捕获所有异常，确保单股失败不影响整体
            logger.exception(f"[{code}] 处理过程发生未知异常: {e}")
            return None
    
    def run(
        self, 
        stock_codes: Optional[List[str]] = None,
        dry_run: bool = False,
        send_notification: bool = True
    ) -> List[AnalysisResult]:
        """
        运行完整的分析流程
        
        流程：
        1. 获取待分析的股票列表
        2. 使用线程池并发处理
        3. 收集分析结果
        4. 发送通知
        
        Args:
            stock_codes: 股票代码列表（可选，默认使用配置中的自选股）
            dry_run: 是否仅获取数据不分析
            send_notification: 是否发送推送通知
            
        Returns:
            分析结果列表
        """
        start_time = time.time()
        
        # 使用配置中的股票列表
        if stock_codes is None:
            stock_codes = self.config.stock_list
        
        if not stock_codes:
            logger.error("未配置自选股列表，请在 .env 文件中设置 STOCK_LIST")
            return []
        
        # 自动记录持仓快照（不影响主流程）
        try:
            snapshot_count = self.db.save_daily_snapshot()
            logger.info(f"持仓快照记录完成，新增 {snapshot_count} 条")
        except Exception as e:
            logger.warning(f"持仓快照记录失败: {e}")
        
        logger.info(f"===== 开始分析 {len(stock_codes)} 只股票 =====")
        logger.info(f"股票列表: {', '.join(stock_codes)}")
        logger.info(f"并发数: {self.max_workers}, 模式: {'仅获取数据' if dry_run else '完整分析'}")
        
        results: List[AnalysisResult] = []
        
        # 使用线程池并发处理
        # 注意：max_workers 设置较低（默认3）以避免触发反爬
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # 提交任务
            future_to_code = {
                executor.submit(
                    self.process_single_stock, 
                    code, 
                    skip_analysis=dry_run
                ): code
                for code in stock_codes
            }
            
            # 收集结果
            for future in as_completed(future_to_code):
                code = future_to_code[future]
                try:
                    result = future.result()
                    if result:
                        results.append(result)
                except Exception as e:
                    logger.error(f"[{code}] 任务执行失败: {e}")
        
        # 统计
        elapsed_time = time.time() - start_time
        
        # dry-run 模式下，数据获取成功即视为成功
        if dry_run:
            # 检查哪些股票的数据今天已存在
            success_count = sum(1 for code in stock_codes if self.db.has_today_data(code))
            fail_count = len(stock_codes) - success_count
        else:
            success_count = len(results)
            fail_count = len(stock_codes) - success_count
        
        logger.info(f"===== 分析完成 =====")
        logger.info(f"成功: {success_count}, 失败: {fail_count}, 耗时: {elapsed_time:.2f} 秒")
        
        # 发送通知
        if results and send_notification and not dry_run:
            self._send_notifications(results)
        
        return results
    
    def _send_notifications(self, results: List[AnalysisResult]) -> None:
        """
        发送分析结果通知
        
        生成决策仪表盘格式的报告
        
        Args:
            results: 分析结果列表
        """
        try:
            logger.info("生成决策仪表盘日报...")
            
            # 生成决策仪表盘格式的详细日报
            report = self.notifier.generate_dashboard_report(results)
            
            # 保存到本地
            filepath = self.notifier.save_report_to_file(report)
            logger.info(f"决策仪表盘日报已保存: {filepath}")
            
            # 推送到企业微信/飞书（使用分条发送模式）
            if self.notifier.is_available():
                notification_type = self.notifier._get_notification_type()
                logger.info(f"开始推送{notification_type}通知（分条独立发送）...")
                self.notifier.send_batch_notifications(results)
            else:
                logger.info("企业微信/飞书未配置，跳过推送")
                
        except Exception as e:
            logger.error(f"发送通知失败: {e}")


def _load_positions_csv(filepath: str) -> Dict[str, Dict[str, Any]]:
    """读取持仓 CSV 文件"""
    positions: Dict[str, Dict[str, Any]] = {}
    with open(filepath, newline='', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            code = (row.get('code') or '').strip()
            if not code:
                continue
            try:
                cost_price = float(row.get('cost_price', 0) or 0)
                quantity = int(float(row.get('quantity', 0) or 0))
            except ValueError:
                continue
            positions[code] = {
                'cost_price': cost_price,
                'quantity': quantity,
                'note': (row.get('note') or '').strip()
            }
    return positions


def _export_positions_csv(filepath: str, positions: List[Any]) -> None:
    """导出持仓到 CSV 文件"""
    with open(filepath, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(['code', 'cost_price', 'quantity', 'note'])
        for pos in positions:
            writer.writerow([pos.code, pos.cost_price, pos.quantity, ''])


def _get_latest_close(db: DatabaseManager, code: str) -> Optional[float]:
    """获取最新收盘价"""
    latest = db.get_latest_data(code, days=1)
    if latest and latest[0].close:
        return latest[0].close
    return None


def _import_positions_from_csv(
    db: DatabaseManager,
    filepath: str,
    trade_date: Optional[date] = None
) -> Dict[str, int]:
    """
    从 CSV 导入持仓并记录交易变动
    """
    if trade_date is None:
        trade_date = date.today()
    
    csv_positions = _load_positions_csv(filepath)
    db_positions = {p.code: p for p in db.list_positions()}
    
    counters = {'buy': 0, 'sell': 0, 'update': 0, 'remove': 0, 'no_change': 0}
    
    # 处理 CSV 中的持仓
    for code, new_pos in csv_positions.items():
        new_qty = new_pos['quantity']
        new_cost = new_pos['cost_price']
        note = new_pos.get('note') or None
        
        if new_qty <= 0:
            # 视为清仓
            if code in db_positions:
                old = db_positions[code]
                sell_price = _get_latest_close(db, code) or old.cost_price
                db.add_trade_log(trade_date, code, 'SELL', sell_price, old.quantity, note or 'CSV清仓')
                db.remove_position(code)
                counters['remove'] += 1
            continue
        
        if code not in db_positions:
            # 新增持仓
            db.upsert_position(code, new_cost, new_qty)
            db.add_trade_log(trade_date, code, 'BUY', new_cost, new_qty, note or 'CSV新增')
            counters['buy'] += 1
            continue
        
        old = db_positions[code]
        if new_qty > old.quantity:
            diff = new_qty - old.quantity
            db.upsert_position(code, new_cost, new_qty)
            db.add_trade_log(trade_date, code, 'BUY', new_cost, diff, note or 'CSV加仓')
            counters['buy'] += 1
        elif new_qty < old.quantity:
            diff = old.quantity - new_qty
            sell_price = _get_latest_close(db, code) or new_cost or old.cost_price
            db.upsert_position(code, new_cost, new_qty)
            db.add_trade_log(trade_date, code, 'SELL', sell_price, diff, note or 'CSV减仓')
            counters['sell'] += 1
        elif new_cost != old.cost_price:
            db.upsert_position(code, new_cost, new_qty)
            counters['update'] += 1
        else:
            counters['no_change'] += 1
    
    # CSV 不存在的持仓视为清仓
    for code, old in db_positions.items():
        if code not in csv_positions:
            sell_price = _get_latest_close(db, code) or old.cost_price
            db.add_trade_log(trade_date, code, 'SELL', sell_price, old.quantity, 'CSV清仓')
            db.remove_position(code)
            counters['remove'] += 1
    
    return counters


def parse_arguments() -> argparse.Namespace:
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description='A股自选股智能分析系统',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
示例:
  python main.py                    # 正常运行
  python main.py --debug            # 调试模式
  python main.py --dry-run          # 仅获取数据，不进行 AI 分析
  python main.py --stocks 600519,000001  # 指定分析特定股票
  python main.py --no-notify        # 不发送推送通知
  python main.py --schedule         # 启用定时任务模式
  python main.py --market-review    # 仅运行大盘复盘
  python main.py --position add 600519 --cost 1800.50 --qty 100
  python main.py --position list
  python main.py --position remove 600519
        '''
    )
    
    parser.add_argument(
        '--debug',
        action='store_true',
        help='启用调试模式，输出详细日志'
    )
    
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='仅获取数据，不进行 AI 分析'
    )
    
    parser.add_argument(
        '--stocks',
        type=str,
        help='指定要分析的股票代码，逗号分隔（覆盖配置文件）'
    )

    parser.add_argument(
        '--use-positions',
        action='store_true',
        help='优先使用持仓列表作为分析股票'
    )
    
    parser.add_argument(
        '--no-notify',
        action='store_true',
        help='不发送推送通知'
    )
    
    parser.add_argument(
        '--workers',
        type=int,
        default=None,
        help='并发线程数（默认使用配置值）'
    )
    
    parser.add_argument(
        '--schedule',
        action='store_true',
        help='启用定时任务模式，每日定时执行'
    )
    
    parser.add_argument(
        '--market-review',
        action='store_true',
        help='仅运行大盘复盘分析'
    )
    
    parser.add_argument(
        '--no-market-review',
        action='store_true',
        help='跳过大盘复盘分析'
    )

    parser.add_argument(
        '--capital',
        choices=['set', 'status'],
        help='资金管理：set/status'
    )
    parser.add_argument(
        '--capital-amount',
        type=float,
        default=None,
        help='总资金金额（配合 --capital set 使用）'
    )
    parser.add_argument(
        '--risk',
        type=float,
        default=None,
        help='单笔风险比例（默认 0.02）'
    )
    parser.add_argument(
        '--max-position',
        type=float,
        default=None,
        help='单股最大仓位比例（默认 0.30）'
    )

    parser.add_argument(
        '--position',
        choices=['add', 'list', 'remove', 'import', 'export', 'history', 'trades'],
        help='持仓管理：add/list/remove/import/export/history/trades'
    )
    parser.add_argument(
        'position_code',
        nargs='?',
        help='持仓股票代码（配合 --position 使用）'
    )
    parser.add_argument(
        '--cost',
        type=float,
        default=None,
        help='持仓成本价（仅 add 使用）'
    )
    parser.add_argument(
        '--qty',
        type=int,
        default=None,
        help='持仓数量（仅 add 使用）'
    )
    
    return parser.parse_args()


def run_market_review(notifier: NotificationService, analyzer=None, search_service=None) -> Optional[str]:
    """
    执行大盘复盘分析
    
    Args:
        notifier: 通知服务
        analyzer: AI分析器（可选）
        search_service: 搜索服务（可选）
    
    Returns:
        复盘报告文本
    """
    logger.info("开始执行大盘复盘分析...")
    
    try:
        market_analyzer = MarketAnalyzer(
            search_service=search_service,
            analyzer=analyzer
        )
        
        # 执行复盘
        review_report = market_analyzer.run_daily_review()
        
        if review_report:
            # 推送到企业微信/飞书
            if notifier.is_available():
                # 添加标题
                report_content = f"## 🎯 大盘复盘\n\n{review_report}"
                # 飞书限制 4000 字符，企业微信限制 4096 字节
                if len(report_content) > 3800:
                    report_content = report_content[:3800] + "\n...(已截断)"
                
                success = notifier.send_to_wechat(report_content)
                notification_type = notifier._get_notification_type()
                if success:
                    logger.info(f"大盘复盘推送成功（{notification_type}）")
                else:
                    logger.warning(f"大盘复盘推送失败（{notification_type}）")
            
            return review_report
        
    except Exception as e:
        logger.error(f"大盘复盘分析失败: {e}")
    
    return None


def run_full_analysis(
    config: Config,
    args: argparse.Namespace,
    stock_codes: Optional[List[str]] = None
):
    """
    执行完整的分析流程（个股 + 大盘复盘）
    
    这是定时任务调用的主函数
    """
    try:
        # 创建调度器
        pipeline = StockAnalysisPipeline(
            config=config,
            max_workers=args.workers
        )
        
        # 1. 运行个股分析
        results = pipeline.run(
            stock_codes=stock_codes,
            dry_run=args.dry_run,
            send_notification=not args.no_notify
        )
        
        # 2. 运行大盘复盘（如果启用且不是仅个股模式）
        if config.market_review_enabled and not args.no_market_review:
            run_market_review(
                notifier=pipeline.notifier,
                analyzer=pipeline.analyzer,
                search_service=pipeline.search_service
            )
        
        # 输出摘要
        if results:
            logger.info("\n===== 分析结果摘要 =====")
            for r in sorted(results, key=lambda x: x.sentiment_score, reverse=True):
                emoji = r.get_emoji()
                logger.info(
                    f"{emoji} {r.name}({r.code}): {r.operation_advice} | "
                    f"评分 {r.sentiment_score} | {r.trend_prediction}"
                )
        
        logger.info("\n任务执行完成")
        
    except Exception as e:
        logger.exception(f"分析流程执行失败: {e}")


def main() -> int:
    """
    主入口函数
    
    Returns:
        退出码（0 表示成功）
    """
    # 解析命令行参数
    args = parse_arguments()
    
    # 加载配置（在设置日志前加载，以获取日志目录）
    config = get_config()
    
    # 配置日志（输出到控制台和文件）
    setup_logging(debug=args.debug, log_dir=config.log_dir)
    
    logger.info("=" * 60)
    logger.info("A股自选股智能分析系统 启动")
    logger.info(f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)
    
    # 验证配置
    warnings = config.validate()
    for warning in warnings:
        logger.warning(warning)
    
    # 资金管理模式
    if args.capital:
        db = get_db()
        action = args.capital
        
        if action == 'status':
            summary = db.get_portfolio_summary()
            if summary.get('error'):
                logger.warning(summary['error'])
                logger.info("请先设置总资金：python main.py --capital set 500000")
                return 2
            
            logger.info(f"总资金: {summary['total_capital']:.2f} 元")
            logger.info(f"已投入: {summary['invested']:.2f} 元 ({summary['invested_ratio'] * 100:.1f}%)")
            logger.info(f"可用资金: {summary['available']:.2f} 元")
            logger.info(f"总风险敞口: {summary['total_risk_exposure']:.2f} 元")
            logger.info(f"单笔风险比例: {summary['risk_per_trade']:.2%}")
            logger.info(f"单股最大仓位: {summary['max_position_ratio']:.2%}")
            
            if summary['holdings']:
                logger.info("持仓明细:")
                for h in summary['holdings']:
                    logger.info(
                        f"- {h['code']}: 成本{h['cost_price']:.2f} "
                        f"数量{h['quantity']} 市值{h['market_value']:.2f} "
                        f"占比{h['position_ratio'] * 100:.1f}% "
                        f"盈亏{h['profit_pct']:.2f}% "
                        f"风险敞口{h['risk_exposure']:.2f}元"
                    )
            else:
                logger.info("当前无持仓记录")
            return 0
        
        if action == 'set':
            amount = args.capital_amount
            if amount is None and args.position_code:
                # 兼容: --capital set 500000
                try:
                    amount = float(args.position_code)
                except ValueError:
                    amount = None
            if amount is None or amount <= 0:
                logger.error("请提供有效的总资金金额，例如：python main.py --capital set 500000")
                return 2
            
            risk_per_trade = args.risk if args.risk is not None else 0.02
            max_position_ratio = args.max_position if args.max_position is not None else 0.30
            capital = db.set_capital(amount, risk_per_trade, max_position_ratio)
            logger.info(
                f"资金配置已更新: 总资金 {capital.total_capital:.2f} | "
                f"单笔风险 {capital.risk_per_trade:.2%} | "
                f"单股最大仓位 {capital.max_position_ratio:.2%}"
            )
            return 0
    
    # 持仓管理模式
    if args.position:
        db = get_db()
        action = args.position
        code = args.position_code
        csv_path = os.path.join(os.path.dirname(__file__), 'positions.csv')
        
        if action == 'list':
            positions = db.list_positions()
            if not positions:
                logger.info("当前无持仓记录")
            else:
                logger.info("当前持仓列表:")
                for p in positions:
                    logger.info(f"- {p.code}: 成本 {p.cost_price:.2f} | 数量 {p.quantity}")
            return 0

        if action == 'import':
            if not os.path.exists(csv_path):
                logger.error(f"未找到持仓文件: {csv_path}")
                return 2
            counters = _import_positions_from_csv(db, csv_path)
            logger.info(
                f"导入完成: 买入{counters['buy']} | "
                f"卖出{counters['sell']} | 更新{counters['update']} | "
                f"清仓{counters['remove']} | 无变化{counters['no_change']}"
            )
            return 0

        if action == 'export':
            positions = db.list_positions()
            _export_positions_csv(csv_path, positions)
            logger.info(f"已导出持仓到: {csv_path}")
            return 0

        if action == 'history':
            if not code:
                logger.error("请提供股票代码，例如：--position history 600519")
                return 2
            history = db.list_position_history(code)
            if not history:
                logger.info(f"未找到 {code} 的持仓历史")
                return 0
            logger.info(f"{code} 持仓历史:")
            for h in history:
                logger.info(
                    f"- {h.date}: 数量 {h.quantity} | "
                    f"收盘 {h.close_price:.2f} | "
                    f"盈亏 {h.profit_loss:.2f} ({h.profit_pct:.2f}%)"
                )
            return 0

        if action == 'trades':
            logs = db.list_trade_logs(code=code)
            if not logs:
                logger.info("未找到交易记录")
                return 0
            logger.info("交易记录:")
            for log in logs:
                logger.info(
                    f"- {log.trade_date} {log.action} {log.code} "
                    f"{log.quantity} @ {log.price:.2f} "
                    f"备注: {log.note or ''}"
                )
            return 0
        
        if not code:
            logger.error("请提供持仓股票代码，例如：--position add 600519")
            return 2
        
        if action == 'add':
            if args.cost is None or args.qty is None:
                logger.error("新增持仓需要提供 --cost 和 --qty")
                return 2
            if args.qty <= 0 or args.cost <= 0:
                logger.error("持仓数量和成本价必须大于 0")
                return 2
            position = db.upsert_position(code, args.cost, args.qty)
            logger.info(f"持仓已更新: {position.code} | 成本 {position.cost_price:.2f} | 数量 {position.quantity}")
            return 0
        
        if action == 'remove':
            removed = db.remove_position(code)
            if removed:
                logger.info(f"已删除持仓: {code}")
                return 0
            logger.warning(f"未找到持仓记录: {code}")
            return 1
    
    # 解析股票列表
    stock_codes = None
    if args.stocks:
        stock_codes = [code.strip() for code in args.stocks.split(',') if code.strip()]
        logger.info(f"使用命令行指定的股票列表: {stock_codes}")
    elif args.use_positions:
        db = get_db()
        positions = db.list_positions()
        stock_codes = [p.code for p in positions]
        logger.info(f"使用持仓列表作为分析股票: {stock_codes}")
    
    try:
        # 模式1: 仅大盘复盘
        if args.market_review:
            logger.info("模式: 仅大盘复盘")
            notifier = NotificationService(
                webhook_url=config.wechat_webhook_url,
                feishu_webhook_url=config.feishu_webhook_url
            )
            
            # 初始化搜索服务和分析器（如果有配置）
            search_service = None
            analyzer = None
            
            if config.tavily_api_keys or config.serpapi_keys:
                search_service = SearchService(
                    tavily_keys=config.tavily_api_keys,
                    serpapi_keys=config.serpapi_keys
                )
            
            if config.gemini_api_key:
                analyzer = GeminiAnalyzer(api_key=config.gemini_api_key)
            
            run_market_review(notifier, analyzer, search_service)
            return 0
        
        # 模式2: 定时任务模式
        if args.schedule or config.schedule_enabled:
            logger.info("模式: 定时任务")
            logger.info(f"每日执行时间: {config.schedule_time}")
            
            from scheduler import run_with_schedule
            
            def scheduled_task():
                run_full_analysis(config, args, stock_codes)
            
            run_with_schedule(
                task=scheduled_task,
                schedule_time=config.schedule_time,
                run_immediately=True  # 启动时先执行一次
            )
            return 0
        
        # 模式3: 正常单次运行
        run_full_analysis(config, args, stock_codes)
        
        logger.info("\n程序执行完成")
        return 0
        
    except KeyboardInterrupt:
        logger.info("\n用户中断，程序退出")
        return 130
        
    except Exception as e:
        logger.exception(f"程序执行失败: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
