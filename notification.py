# -*- coding: utf-8 -*-
"""
===================================
A股自选股智能分析系统 - 通知层
===================================

职责：
1. 汇总分析结果生成日报
2. 支持 Markdown 格式输出
3. 推送到企业微信 Webhook
"""

import logging
from datetime import datetime
from typing import List, Dict, Any, Optional

import requests

from config import get_config
from analyzer import AnalysisResult

logger = logging.getLogger(__name__)


class NotificationService:
    """
    通知服务
    
    职责：
    1. 生成 Markdown 格式的分析日报
    2. 推送消息到企业微信/飞书机器人
    3. 支持本地保存日报
    """
    
    def __init__(self, webhook_url: Optional[str] = None, feishu_webhook_url: Optional[str] = None):
        """
        初始化通知服务
        
        Args:
            webhook_url: 企业微信 Webhook URL（可选，默认从配置读取）
            feishu_webhook_url: 飞书 Webhook URL（可选，默认从配置读取）
        """
        config = get_config()
        self._webhook_url = webhook_url or config.wechat_webhook_url
        self._feishu_webhook_url = feishu_webhook_url or config.feishu_webhook_url
        
        if not self._webhook_url and not self._feishu_webhook_url:
            logger.warning("企业微信和飞书 Webhook URL 均未配置，将不发送推送通知")
        elif self._feishu_webhook_url:
            logger.info("已配置飞书 Webhook，将推送到飞书群机器人")
        elif self._webhook_url:
            logger.info("已配置企业微信 Webhook，将推送到企业微信")
    
    def is_available(self) -> bool:
        """检查通知服务是否可用"""
        return bool(self._webhook_url) or bool(self._feishu_webhook_url)
    
    def _get_notification_type(self) -> str:
        """获取当前使用的通知类型"""
        if self._feishu_webhook_url:
            return "飞书"
        elif self._webhook_url:
            return "企业微信"
        return "未知"
    
    def generate_daily_report(
        self, 
        results: List[AnalysisResult],
        report_date: Optional[str] = None
    ) -> str:
        """
        生成 Markdown 格式的日报（详细版）
        
        Args:
            results: 分析结果列表
            report_date: 报告日期（默认今天）
            
        Returns:
            Markdown 格式的日报内容
        """
        if report_date is None:
            report_date = datetime.now().strftime('%Y-%m-%d')
        
        # 标题
        report_lines = [
            f"# 📅 {report_date} A股自选股智能分析报告",
            "",
            f"> 共分析 **{len(results)}** 只股票 | 报告生成时间：{datetime.now().strftime('%H:%M:%S')}",
            "",
            "---",
            "",
        ]
        
        # 按评分排序（高分在前）
        sorted_results = sorted(
            results, 
            key=lambda x: x.sentiment_score, 
            reverse=True
        )
        
        # 统计信息
        buy_count = sum(1 for r in results if r.operation_advice in ['买入', '加仓', '强烈买入'])
        sell_count = sum(1 for r in results if r.operation_advice in ['卖出', '减仓', '强烈卖出'])
        hold_count = sum(1 for r in results if r.operation_advice in ['持有', '观望'])
        avg_score = sum(r.sentiment_score for r in results) / len(results) if results else 0
        
        report_lines.extend([
            "## 📊 操作建议汇总",
            "",
            f"| 指标 | 数值 |",
            f"|------|------|",
            f"| 🟢 建议买入/加仓 | **{buy_count}** 只 |",
            f"| 🟡 建议持有/观望 | **{hold_count}** 只 |",
            f"| 🔴 建议减仓/卖出 | **{sell_count}** 只 |",
            f"| 📈 平均看多评分 | **{avg_score:.1f}** 分 |",
            "",
            "---",
            "",
            "## 📈 个股详细分析",
            "",
        ])
        
        # 逐个股票的详细分析
        for result in sorted_results:
            emoji = result.get_emoji()
            confidence_stars = result.get_confidence_stars() if hasattr(result, 'get_confidence_stars') else '⭐⭐'
            
            report_lines.extend([
                f"### {emoji} {result.name} ({result.code})",
                "",
                f"**操作建议：{result.operation_advice}** | **综合评分：{result.sentiment_score}分** | **趋势预测：{result.trend_prediction}** | **置信度：{confidence_stars}**",
                "",
            ])

            # 持仓信息（如有）
            if getattr(result, 'has_position', False) and getattr(result, 'position_info', None):
                pos = result.position_info
                report_lines.extend([
                    "**💼 持仓信息**",
                    f"- 成本价：{pos.get('cost_price', 'N/A')} 元",
                    f"- 持仓量：{pos.get('quantity', 'N/A')} 股",
                    f"- 浮动盈亏：{pos.get('profit_loss', 'N/A')} 元 ({pos.get('profit_pct', 'N/A')}%)",
                    "",
                ])
            
            # 风险管理建议（止损/仓位）
            if getattr(result, 'risk_management', None):
                rm = result.risk_management or {}
                stop_loss = rm.get('stop_loss', {})
                position_sizing = rm.get('position_sizing', {})
                report_lines.extend([
                    "**🛡️ 风险管理**",
                    f"- ATR止损：{stop_loss.get('cost_atr_stop', stop_loss.get('current_atr_stop', 'N/A'))}",
                    f"- MA10止损：{stop_loss.get('ma10_stop', 'N/A')}",
                    f"- 移动止损：{stop_loss.get('trailing_stop', 'N/A')}",
                ])
                if position_sizing:
                    report_lines.append(
                        f"- 建议仓位：{position_sizing.get('suggested_shares', 'N/A')}股 "
                        f"(占比{position_sizing.get('position_ratio', 'N/A')})"
                    )
                report_lines.append("")
            
            # 核心看点
            if hasattr(result, 'key_points') and result.key_points:
                report_lines.extend([
                    f"**🎯 核心看点**：{result.key_points}",
                    "",
                ])
            
            # 买入/卖出理由
            if hasattr(result, 'buy_reason') and result.buy_reason:
                report_lines.extend([
                    f"**💡 操作理由**：{result.buy_reason}",
                    "",
                ])
            
            # 走势分析
            if hasattr(result, 'trend_analysis') and result.trend_analysis:
                report_lines.extend([
                    "#### 📉 走势分析",
                    f"{result.trend_analysis}",
                    "",
                ])
            
            # 短期/中期展望
            outlook_lines = []
            if hasattr(result, 'short_term_outlook') and result.short_term_outlook:
                outlook_lines.append(f"- **短期（1-3日）**：{result.short_term_outlook}")
            if hasattr(result, 'medium_term_outlook') and result.medium_term_outlook:
                outlook_lines.append(f"- **中期（1-2周）**：{result.medium_term_outlook}")
            if outlook_lines:
                report_lines.extend([
                    "#### 🔮 市场展望",
                    *outlook_lines,
                    "",
                ])
            
            # 技术面分析
            tech_lines = []
            if result.technical_analysis:
                tech_lines.append(f"**综合**：{result.technical_analysis}")
            if hasattr(result, 'ma_analysis') and result.ma_analysis:
                tech_lines.append(f"**均线**：{result.ma_analysis}")
            if hasattr(result, 'volume_analysis') and result.volume_analysis:
                tech_lines.append(f"**量能**：{result.volume_analysis}")
            if hasattr(result, 'pattern_analysis') and result.pattern_analysis:
                tech_lines.append(f"**形态**：{result.pattern_analysis}")
            if tech_lines:
                report_lines.extend([
                    "#### 📊 技术面分析",
                    *tech_lines,
                    "",
                ])
            
            # 基本面分析
            fund_lines = []
            if hasattr(result, 'fundamental_analysis') and result.fundamental_analysis:
                fund_lines.append(result.fundamental_analysis)
            if hasattr(result, 'sector_position') and result.sector_position:
                fund_lines.append(f"**板块地位**：{result.sector_position}")
            if hasattr(result, 'company_highlights') and result.company_highlights:
                fund_lines.append(f"**公司亮点**：{result.company_highlights}")
            if fund_lines:
                report_lines.extend([
                    "#### 🏢 基本面分析",
                    *fund_lines,
                    "",
                ])
            
            # 消息面/情绪面
            news_lines = []
            if result.news_summary:
                news_lines.append(f"**新闻摘要**：{result.news_summary}")
            if hasattr(result, 'market_sentiment') and result.market_sentiment:
                news_lines.append(f"**市场情绪**：{result.market_sentiment}")
            if hasattr(result, 'hot_topics') and result.hot_topics:
                news_lines.append(f"**相关热点**：{result.hot_topics}")
            if news_lines:
                report_lines.extend([
                    "#### 📰 消息面/情绪面",
                    *news_lines,
                    "",
                ])
            
            # 综合分析
            if result.analysis_summary:
                report_lines.extend([
                    "#### 📝 综合分析",
                    result.analysis_summary,
                    "",
                ])
            
            # 风险提示
            if hasattr(result, 'risk_warning') and result.risk_warning:
                report_lines.extend([
                    f"⚠️ **风险提示**：{result.risk_warning}",
                    "",
                ])
            
            # 数据来源说明
            if hasattr(result, 'search_performed') and result.search_performed:
                report_lines.append(f"*🔍 已执行联网搜索*")
            if hasattr(result, 'data_sources') and result.data_sources:
                report_lines.append(f"*📋 数据来源：{result.data_sources}*")
            
            # 错误信息（如果有）
            if not result.success and result.error_message:
                report_lines.extend([
                    "",
                    f"❌ **分析异常**：{result.error_message[:100]}",
                ])
            
            report_lines.extend([
                "",
                "---",
                "",
            ])
        
        # 底部信息（去除免责声明）
        report_lines.extend([
            "",
            f"*报告生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*",
        ])
        
        return "\n".join(report_lines)
    
    def _get_signal_level(self, result: AnalysisResult) -> tuple:
        """
        根据操作建议获取信号等级和颜色
        
        Returns:
            (信号文字, emoji, 颜色标记)
        """
        advice = result.operation_advice
        score = result.sentiment_score
        
        if advice in ['强烈买入'] or score >= 80:
            return ('强烈买入', '💚', '强买')
        elif advice in ['买入', '加仓'] or score >= 65:
            return ('买入', '🟢', '买入')
        elif advice in ['持有'] or 55 <= score < 65:
            return ('持有', '🟡', '持有')
        elif advice in ['观望'] or 45 <= score < 55:
            return ('观望', '⚪', '观望')
        elif advice in ['减仓'] or 35 <= score < 45:
            return ('减仓', '🟠', '减仓')
        elif advice in ['卖出', '强烈卖出'] or score < 35:
            return ('卖出', '🔴', '卖出')
        else:
            return ('观望', '⚪', '观望')
    
    def generate_dashboard_report(
        self, 
        results: List[AnalysisResult],
        report_date: Optional[str] = None
    ) -> str:
        """
        生成决策仪表盘格式的日报（详细版）
        
        格式：市场概览 + 重要信息 + 核心结论 + 数据透视 + 作战计划
        
        Args:
            results: 分析结果列表
            report_date: 报告日期（默认今天）
            
        Returns:
            Markdown 格式的决策仪表盘日报
        """
        if report_date is None:
            report_date = datetime.now().strftime('%Y-%m-%d')
        
        # 按评分排序（高分在前）
        sorted_results = sorted(results, key=lambda x: x.sentiment_score, reverse=True)
        
        # 统计信息
        buy_count = sum(1 for r in results if r.operation_advice in ['买入', '加仓', '强烈买入'])
        sell_count = sum(1 for r in results if r.operation_advice in ['卖出', '减仓', '强烈卖出'])
        hold_count = sum(1 for r in results if r.operation_advice in ['持有', '观望'])
        
        report_lines = [
            f"# 🎯 {report_date} 决策仪表盘",
            "",
            f"> 共分析 **{len(results)}** 只股票 | 🟢买入:{buy_count} 🟡观望:{hold_count} 🔴卖出:{sell_count}",
            "",
            "---",
            "",
        ]
        
        # 逐个股票的决策仪表盘
        for result in sorted_results:
            signal_text, signal_emoji, signal_tag = self._get_signal_level(result)
            dashboard = result.dashboard if hasattr(result, 'dashboard') and result.dashboard else {}
            
            # 股票名称（优先使用 dashboard 或 result 中的名称）
            stock_name = result.name if result.name and not result.name.startswith('股票') else f'股票{result.code}'
            
            report_lines.extend([
                f"## {signal_emoji} {stock_name} ({result.code})",
                "",
            ])
            
            # ========== 舆情与基本面概览（放在最前面）==========
            intel = dashboard.get('intelligence', {}) if dashboard else {}
            if intel:
                report_lines.extend([
                    "### 📰 重要信息速览",
                    "",
                ])
                
                # 舆情情绪总结
                if intel.get('sentiment_summary'):
                    report_lines.append(f"**💭 舆情情绪**: {intel['sentiment_summary']}")
                
                # 业绩预期
                if intel.get('earnings_outlook'):
                    report_lines.append(f"**📊 业绩预期**: {intel['earnings_outlook']}")
                
                # 风险警报（醒目显示）
                risk_alerts = intel.get('risk_alerts', [])
                if risk_alerts:
                    report_lines.append("")
                    report_lines.append("**🚨 风险警报**:")
                    for alert in risk_alerts:
                        report_lines.append(f"- {alert}")
                
                # 利好催化
                catalysts = intel.get('positive_catalysts', [])
                if catalysts:
                    report_lines.append("")
                    report_lines.append("**✨ 利好催化**:")
                    for cat in catalysts:
                        report_lines.append(f"- {cat}")
                
                # 最新消息
                if intel.get('latest_news'):
                    report_lines.append("")
                    report_lines.append(f"**📢 最新动态**: {intel['latest_news']}")
                
                report_lines.append("")
            
            # ========== 核心结论 ==========
            core = dashboard.get('core_conclusion', {}) if dashboard else {}
            one_sentence = core.get('one_sentence', result.analysis_summary)
            time_sense = core.get('time_sensitivity', '本周内')
            pos_advice = core.get('position_advice', {})
            
            report_lines.extend([
                "### 📌 核心结论",
                "",
                f"**{signal_emoji} {signal_text}** | {result.trend_prediction}",
                "",
                f"> **一句话决策**: {one_sentence}",
                "",
                f"⏰ **时效性**: {time_sense}",
                "",
            ])

            # 持仓信息（如有）
            if getattr(result, 'has_position', False) and getattr(result, 'position_info', None):
                pos = result.position_info
                report_lines.extend([
                    "**💼 持仓概览**",
                    "",
                    f"- 成本价：{pos.get('cost_price', 'N/A')} 元",
                    f"- 持仓量：{pos.get('quantity', 'N/A')} 股",
                    f"- 浮动盈亏：{pos.get('profit_loss', 'N/A')} 元 ({pos.get('profit_pct', 'N/A')}%)",
                    "",
                ])
            
            if getattr(result, 'risk_management', None):
                rm = result.risk_management or {}
                stop_loss = rm.get('stop_loss', {})
                position_sizing = rm.get('position_sizing', {})
                report_lines.extend([
                    "**🛡️ 风险管理**",
                    "",
                    f"- ATR止损：{stop_loss.get('cost_atr_stop', stop_loss.get('current_atr_stop', 'N/A'))}",
                    f"- MA10止损：{stop_loss.get('ma10_stop', 'N/A')}",
                    f"- 移动止损：{stop_loss.get('trailing_stop', 'N/A')}",
                ])
                if position_sizing:
                    report_lines.append(
                        f"- 建议仓位：{position_sizing.get('suggested_shares', 'N/A')}股 "
                        f"(占比{position_sizing.get('position_ratio', 'N/A')})"
                    )
                report_lines.append("")
            
            # 持仓分类建议
            if pos_advice:
                report_lines.extend([
                    "| 持仓情况 | 操作建议 |",
                    "|---------|---------|",
                    f"| 🆕 **空仓者** | {pos_advice.get('no_position', result.operation_advice)} |",
                    f"| 💼 **持仓者** | {pos_advice.get('has_position', '继续持有')} |",
                    "",
                ])
            
            # ========== 数据透视 ==========
            data_persp = dashboard.get('data_perspective', {}) if dashboard else {}
            if data_persp:
                trend_data = data_persp.get('trend_status', {})
                price_data = data_persp.get('price_position', {})
                vol_data = data_persp.get('volume_analysis', {})
                chip_data = data_persp.get('chip_structure', {})
                
                report_lines.extend([
                    "### 📊 数据透视",
                    "",
                ])
                
                # 趋势状态
                if trend_data:
                    is_bullish = "✅ 是" if trend_data.get('is_bullish', False) else "❌ 否"
                    report_lines.extend([
                        f"**均线排列**: {trend_data.get('ma_alignment', 'N/A')} | 多头排列: {is_bullish} | 趋势强度: {trend_data.get('trend_score', 'N/A')}/100",
                        "",
                    ])
                
                # 价格位置
                if price_data:
                    bias_status = price_data.get('bias_status', 'N/A')
                    bias_emoji = "✅" if bias_status == "安全" else ("⚠️" if bias_status == "警戒" else "🚨")
                    report_lines.extend([
                        "| 价格指标 | 数值 |",
                        "|---------|------|",
                        f"| 当前价 | {price_data.get('current_price', 'N/A')} |",
                        f"| MA5 | {price_data.get('ma5', 'N/A')} |",
                        f"| MA10 | {price_data.get('ma10', 'N/A')} |",
                        f"| MA20 | {price_data.get('ma20', 'N/A')} |",
                        f"| 乖离率(MA5) | {price_data.get('bias_ma5', 'N/A')}% {bias_emoji}{bias_status} |",
                        f"| 支撑位 | {price_data.get('support_level', 'N/A')} |",
                        f"| 压力位 | {price_data.get('resistance_level', 'N/A')} |",
                        "",
                    ])
                
                # 量能分析
                if vol_data:
                    report_lines.extend([
                        f"**量能**: 量比 {vol_data.get('volume_ratio', 'N/A')} ({vol_data.get('volume_status', '')}) | 换手率 {vol_data.get('turnover_rate', 'N/A')}%",
                        f"💡 *{vol_data.get('volume_meaning', '')}*",
                        "",
                    ])
                
                # 筹码结构
                if chip_data:
                    chip_health = chip_data.get('chip_health', 'N/A')
                    chip_emoji = "✅" if chip_health == "健康" else ("⚠️" if chip_health == "一般" else "🚨")
                    report_lines.extend([
                        f"**筹码**: 获利比例 {chip_data.get('profit_ratio', 'N/A')} | 平均成本 {chip_data.get('avg_cost', 'N/A')} | 集中度 {chip_data.get('concentration', 'N/A')} {chip_emoji}{chip_health}",
                        "",
                    ])
            
            # 舆情情报已移至顶部显示
            
            # ========== 作战计划 ==========
            battle = dashboard.get('battle_plan', {}) if dashboard else {}
            if battle:
                report_lines.extend([
                    "### 🎯 作战计划",
                    "",
                ])
                
                # 狙击点位
                sniper = battle.get('sniper_points', {})
                if sniper:
                    report_lines.extend([
                        "**📍 狙击点位**",
                        "",
                        "| 点位类型 | 价格 |",
                        "|---------|------|",
                        f"| 🎯 理想买入点 | {sniper.get('ideal_buy', 'N/A')} |",
                        f"| 🔵 次优买入点 | {sniper.get('secondary_buy', 'N/A')} |",
                        f"| 🛑 止损位 | {sniper.get('stop_loss', 'N/A')} |",
                        f"| 🎊 目标位 | {sniper.get('take_profit', 'N/A')} |",
                        "",
                    ])
                
                # 仓位策略
                position = battle.get('position_strategy', {})
                if position:
                    report_lines.extend([
                        f"**💰 仓位建议**: {position.get('suggested_position', 'N/A')}",
                        f"- 建仓策略: {position.get('entry_plan', 'N/A')}",
                        f"- 风控策略: {position.get('risk_control', 'N/A')}",
                        "",
                    ])
                
                # 检查清单
                checklist = battle.get('action_checklist', [])
                if checklist:
                    report_lines.extend([
                        "**✅ 检查清单**",
                        "",
                    ])
                    for item in checklist:
                        report_lines.append(f"- {item}")
                    report_lines.append("")
            
            # 如果没有 dashboard，显示传统格式
            if not dashboard:
                # 操作理由
                if result.buy_reason:
                    report_lines.extend([
                        f"**💡 操作理由**: {result.buy_reason}",
                        "",
                    ])
                
                # 风险提示
                if result.risk_warning:
                    report_lines.extend([
                        f"**⚠️ 风险提示**: {result.risk_warning}",
                        "",
                    ])
                
                # 技术面分析
                if result.ma_analysis or result.volume_analysis:
                    report_lines.extend([
                        "### 📊 技术面",
                        "",
                    ])
                    if result.ma_analysis:
                        report_lines.append(f"**均线**: {result.ma_analysis}")
                    if result.volume_analysis:
                        report_lines.append(f"**量能**: {result.volume_analysis}")
                    report_lines.append("")
                
                # 消息面
                if result.news_summary:
                    report_lines.extend([
                        "### 📰 消息面",
                        f"{result.news_summary}",
                        "",
                    ])
            
            report_lines.extend([
                "---",
                "",
            ])
        
        # 底部（去除免责声明）
        report_lines.extend([
            "",
            f"*报告生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*",
        ])
        
        return "\n".join(report_lines)
    
    def format_wechat_summary(self, results: List[AnalysisResult]) -> str:
        """
        生成企业微信日报头部的汇总信息
        """
        report_date = datetime.now().strftime('%Y-%m-%d')
        
        # 统计
        buy_count = sum(1 for r in results if r.operation_advice in ['买入', '加仓', '强烈买入'])
        sell_count = sum(1 for r in results if r.operation_advice in ['卖出', '减仓', '强烈卖出'])
        hold_count = sum(1 for r in results if r.operation_advice in ['持有', '观望'])
        
        lines = [
            f"## 📅 {report_date} 决策仪表盘",
            "",
            f"> 共分析 **{len(results)}** 只股票",
            f"> 🟢 买入: {buy_count} 只",
            f"> 🟡 观望: {hold_count} 只",
            f"> 🔴 卖出: {sell_count} 只",
            "",
            "👇 *详细个股分析见下方独立消息*",
        ]
        return "\n".join(lines)

    def format_wechat_stock_msg(self, result: AnalysisResult) -> str:
        """
        生成单只股票的企业微信消息
        """
        lines = []
        signal_text, signal_emoji, _ = self._get_signal_level(result)
        dashboard = result.dashboard if hasattr(result, 'dashboard') and result.dashboard else {}
        core = dashboard.get('core_conclusion', {}) if dashboard else {}
        battle = dashboard.get('battle_plan', {}) if dashboard else {}
        intel = dashboard.get('intelligence', {}) if dashboard else {}
        
        # 股票名称
        stock_name = result.name if result.name and not result.name.startswith('股票') else f'股票{result.code}'
        
        # 标题行：信号等级 + 股票名称
        lines.append(f"### {signal_emoji} **{signal_text}** | {stock_name}({result.code})")
        lines.append("")
        
        # 核心决策（一句话）
        one_sentence = core.get('one_sentence', result.analysis_summary) if core else result.analysis_summary
        if one_sentence:
            lines.append(f"📌 **{one_sentence}**")
            lines.append("")
        
        # SCore & Trend
        lines.append(f"评分: {result.sentiment_score}分 | 趋势: {result.trend_prediction}")
        lines.append("")
        
        # 重要信息区（舆情+基本面）
        info_lines = []
        
        # 业绩预期
        if intel.get('earnings_outlook'):
            outlook = intel['earnings_outlook']
            info_lines.append(f"📊 业绩: {outlook}")
        
        # 舆情情绪
        if intel.get('sentiment_summary'):
            sentiment = intel['sentiment_summary']
            info_lines.append(f"💭 舆情: {sentiment}")
        
        if info_lines:
            lines.extend(info_lines)
            lines.append("")
        
        # 风险警报（最重要，醒目显示）
        risks = intel.get('risk_alerts', []) if intel else []
        if risks:
            lines.append("🚨 **风险**:")
            for risk in risks: 
                lines.append(f"   • {risk}")
            lines.append("")
        
        # 利好催化
        catalysts = intel.get('positive_catalysts', []) if intel else []
        if catalysts:
            lines.append("✨ **利好**:")
            for cat in catalysts: 
                lines.append(f"   • {cat}")
            lines.append("")
        
        # 狙击点位
        sniper = battle.get('sniper_points', {}) if battle else {}
        if sniper:
            ideal_buy = sniper.get('ideal_buy', '')
            stop_loss = sniper.get('stop_loss', '')
            take_profit = sniper.get('take_profit', '')
            
            points = []
            if ideal_buy:
                points.append(f"🎯买点: **{ideal_buy}**")
            if stop_loss:
                points.append(f"🛑止损: {stop_loss}")
            if take_profit:
                points.append(f"🎊目标: {take_profit}")
            
            if points:
                lines.append(" | ".join(points))
                lines.append("")
        
        # 持仓建议
        pos_advice = core.get('position_advice', {}) if core else {}
        if pos_advice:
            no_pos = pos_advice.get('no_position', '')
            has_pos = pos_advice.get('has_position', '')
            if no_pos:
                lines.append(f"🆕空仓: {no_pos}")
            if has_pos:
                lines.append(f"💼持仓: {has_pos}")
            lines.append("")

        # 持仓盈亏信息
        if getattr(result, 'has_position', False) and getattr(result, 'position_info', None):
            pos = result.position_info
            lines.append(
                f"💼持仓: 成本{pos.get('cost_price', 'N/A')} | "
                f"数量{pos.get('quantity', 'N/A')} | "
                f"盈亏{pos.get('profit_loss', 'N/A')}元({pos.get('profit_pct', 'N/A')}%)"
            )
            lines.append("")
        
        # 止损位提醒
        if getattr(result, 'risk_management', None):
            stop_loss = (result.risk_management or {}).get('stop_loss', {})
            if stop_loss:
                lines.append(
                    f"🛑止损: {stop_loss.get('cost_atr_stop', stop_loss.get('current_atr_stop', 'N/A'))}"
                )
                lines.append("")
        
        # 检查清单
        checklist = battle.get('action_checklist', []) if battle else []
        if checklist:
            # 只显示不通过的项目
            failed_checks = [c for c in checklist if c.startswith('❌') or c.startswith('⚠️')]
            if failed_checks:
                lines.append("**未通过项**:")
                for check in failed_checks:
                    lines.append(f"   {check}")
                lines.append("")
        
        lines.append(f"*生成时间: {datetime.now().strftime('%H:%M')}*")
        
        return "\n".join(lines)

    def send_batch_notifications(self, results: List[AnalysisResult]) -> None:
        """
        批量发送通知（分开发送）
        1. 发送汇总信息
        2. 逐条发送个股信息
        """
        if not self.is_available():
            notification_type = self._get_notification_type()
            logger.warning(f"{notification_type}未配置，跳过推送")
            return

        notification_type = self._get_notification_type()
        logger.info(f"开始推送{notification_type}通知（分条独立发送）...")

        # 1. 发送汇总
        summary_msg = self.format_wechat_summary(results)
        self.send_to_wechat(summary_msg)
        
        # 1.5 发送汇总图片表格（仅企业微信支持，飞书暂时跳过）
        if self._webhook_url:  # 只有企业微信支持图片
            image_path = self.generate_summary_image(results)
            if image_path:
                self.send_image_to_wechat(image_path)
                # 稍作停顿确保图片先到
                import time
                time.sleep(2)
        
        # 2. 逐个发送个股（按评分从高到低）
        sorted_results = sorted(results, key=lambda x: x.sentiment_score, reverse=True)
        
        import time
        for i, result in enumerate(sorted_results):
            # 这里的延迟是为了避免消息乱序和触发频率限制
            time.sleep(1)
            
            msg = self.format_wechat_stock_msg(result)
            logger.info(f"正在推送 {result.name} ({i+1}/{len(results)}) ...")
            self.send_to_wechat(msg)
            
        logger.info(f"所有个股通知推送完成（{notification_type}）")
    
    def generate_wechat_summary(self, results: List[AnalysisResult]) -> str:
        """
        生成企业微信精简版日报（控制在4000字符内）
        
        Args:
            results: 分析结果列表
            
        Returns:
            精简版 Markdown 内容
        """
        report_date = datetime.now().strftime('%Y-%m-%d')
        
        # 按评分排序
        sorted_results = sorted(results, key=lambda x: x.sentiment_score, reverse=True)
        
        # 统计
        buy_count = sum(1 for r in results if r.operation_advice in ['买入', '加仓', '强烈买入'])
        sell_count = sum(1 for r in results if r.operation_advice in ['卖出', '减仓', '强烈卖出'])
        hold_count = sum(1 for r in results if r.operation_advice in ['持有', '观望'])
        avg_score = sum(r.sentiment_score for r in results) / len(results) if results else 0
        
        lines = [
            f"## 📅 {report_date} A股分析报告",
            "",
            f"> 共 **{len(results)}** 只 | 🟢买入:{buy_count} 🟡持有:{hold_count} 🔴卖出:{sell_count} | 均分:{avg_score:.0f}",
            "",
        ]
        
        # 每只股票精简信息（控制长度）
        for result in sorted_results:
            emoji = result.get_emoji()
            
            # 核心信息行
            lines.append(f"### {emoji} {result.name}({result.code})")
            lines.append(f"**{result.operation_advice}** | 评分:{result.sentiment_score} | {result.trend_prediction}")
            
            # 操作理由（截断）
            if hasattr(result, 'buy_reason') and result.buy_reason:
                reason = result.buy_reason[:80] + "..." if len(result.buy_reason) > 80 else result.buy_reason
                lines.append(f"💡 {reason}")
            
            # 核心看点
            if hasattr(result, 'key_points') and result.key_points:
                points = result.key_points[:60] + "..." if len(result.key_points) > 60 else result.key_points
                lines.append(f"🎯 {points}")
            
            # 风险提示（截断）
            if hasattr(result, 'risk_warning') and result.risk_warning:
                risk = result.risk_warning[:50] + "..." if len(result.risk_warning) > 50 else result.risk_warning
                lines.append(f"⚠️ {risk}")
            
            lines.append("")
        
        # 底部
        lines.extend([
            "---",
            "*AI生成，仅供参考，不构成投资建议*",
            f"*详细报告见 reports/report_{report_date.replace('-', '')}.md*"
        ])
        
        content = "\n".join(lines)
        
        # 最终检查长度
        # if len(content) > 3800:
        #     logger.warning(f"精简报告仍超长({len(content)}字符)，进行截断")
        #     content = content[:3800] + "\n\n...(内容过长已截断)"
        
        return content
    
    def send_to_wechat(self, content: str) -> bool:
        """
        推送消息到企业微信/飞书机器人
        
        自动检测配置的 Webhook 类型并发送到对应平台
        
        Args:
            content: Markdown 格式的消息内容
            
        Returns:
            是否全部发送成功
        """
        if not self.is_available():
            logger.warning("Webhook 未配置，跳过推送")
            return False
        
        # 优先使用飞书（如果配置了）
        if self._feishu_webhook_url:
            return self.send_to_feishu(content)
        elif self._webhook_url:
            return self._send_to_wechat_internal(content)
        return False
    
    def _send_to_wechat_internal(self, content: str) -> bool:
        """
        推送消息到企业微信机器人（内部方法）
        
        企业微信 Webhook 消息格式：
        {
            "msgtype": "markdown",
            "markdown": {
                "content": "Markdown 内容"
            }
        }
        
        注意：企业微信 Markdown 限制 4096 字节 (Bytes)
        处理策略：如果超长，自动分割成多条发送
        
        Args:
            content: Markdown 格式的消息内容
            
        Returns:
            是否全部发送成功
        """
        
        # 长度限制（字节数）
        # 这里的 4096 是官方限制，为了安全起见，我们使用 2048 字节作为分块目标
        # 这样即使加上页码等额外信息也不会超标
        MAX_BYTE_LENGTH = 2000
        
        content_bytes = content.encode('utf-8')
        if len(content_bytes) <= MAX_BYTE_LENGTH:
            try:
                return self._send_single_message(content)
            except Exception as e:
                logger.error(f"发送企业微信消息失败: {e}")
                return False
        
        # 内容过长，进行分割
        logger.info(f"消息内容超长({len(content_bytes)}字节)，将分割成多条发送")
        
        chunks = []
        current_chunk_lines = []
        current_chunk_size = 0
        
        lines = content.split('\n')
        
        for line in lines:
            # 计算这一行的字节数（加换行符）
            line_bytes = (line + '\n').encode('utf-8')
            line_size = len(line_bytes)
            
            # 如果单行就超过最大长度（极少见），强制按字节切分
            if line_size > MAX_BYTE_LENGTH:
                # 先保存当前块
                if current_chunk_lines:
                    chunks.append("\n".join(current_chunk_lines))
                    current_chunk_lines = []
                    current_chunk_size = 0
                
                # 这种情况下，我们需要谨慎切分，避免切坏宽字符
                # 简单策略：按字符切分（虽然不完美但安全）
                # 假设平均 3 字节/字符，取 MAX_BYTE_LENGTH / 3 字符
                char_limit = MAX_BYTE_LENGTH // 4 
                for j in range(0, len(line), char_limit):
                    chunks.append(line[j:j+char_limit])
                continue
            
            # 检查是否会超长
            if current_chunk_size + line_size > MAX_BYTE_LENGTH:
                # 保存当前块
                chunks.append("\n".join(current_chunk_lines))
                # 开启新块
                current_chunk_lines = [line]
                current_chunk_size = line_size
            else:
                current_chunk_lines.append(line)
                current_chunk_size += line_size
        
        # 添加最后一个块
        if current_chunk_lines:
            chunks.append("\n".join(current_chunk_lines))
        
        # 发送所有块
        success_count = 0
        total_chunks = len(chunks)
        
        for i, chunk in enumerate(chunks):
            # 添加页码标识
            if total_chunks > 1:
                paginated_content = f"({i+1}/{total_chunks})\n{chunk}"
            else:
                paginated_content = chunk
            
            # 再次检查加上页码后是否超长（极小概率）
            if len(paginated_content.encode('utf-8')) > 4096:
                logger.warning(f"分块 {i+1} 加上页码后仍超长，尝试截断")
                paginated_content = paginated_content[:1000] + "\n...(截断)"
            
            try:
                if self._send_single_message(paginated_content):
                    success_count += 1
                # 稍微延迟，避免触发频率限制
                import time
                time.sleep(0.5)
            except Exception as e:
                logger.error(f"发送第 {i+1} 条消息失败: {e}")
        
        return success_count == total_chunks

    def _send_single_message(self, content: str) -> bool:
        """发送单条消息（根据配置自动选择平台）"""
        if self._feishu_webhook_url:
            return self._send_single_feishu_message(content)
        elif self._webhook_url:
            return self._send_single_wechat_message(content)
        return False
    
    def _send_single_wechat_message(self, content: str) -> bool:
        """发送单条企业微信消息"""
        payload = {
            "msgtype": "markdown",
            "markdown": {
                "content": content
            }
        }
        
        response = requests.post(
            self._webhook_url,
            json=payload,
            timeout=10
        )
        
        if response.status_code == 200:
            result = response.json()
            if result.get('errcode') == 0:
                logger.info("企业微信消息发送成功")
                return True
            else:
                logger.error(f"企业微信返回错误: {result}")
            return False
        return False
    
    def send_to_feishu(self, content: str) -> bool:
        """
        推送消息到飞书群机器人
        
        飞书 Webhook 消息格式：
        {
            "msg_type": "text",
            "content": {
                "text": "文本内容"
            }
        }
        
        或者使用富文本卡片（支持 Markdown）：
        {
            "msg_type": "interactive",
            "card": {
                "config": {
                    "wide_screen_mode": true
                },
                "elements": [
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": "Markdown 内容"
                        }
                    }
                ]
            }
        }
        
        注意：飞书文本消息限制 4096 字符
        处理策略：如果超长，自动分割成多条发送
        
        Args:
            content: Markdown 格式的消息内容
            
        Returns:
            是否全部发送成功
        """
        if not self._feishu_webhook_url:
            logger.warning("飞书 Webhook 未配置，跳过推送")
            return False
        
        # 飞书文本消息限制 4096 字符（不是字节）
        MAX_CHAR_LENGTH = 4000  # 留一些余量
        
        if len(content) <= MAX_CHAR_LENGTH:
            try:
                return self._send_single_feishu_message(content)
            except Exception as e:
                logger.error(f"发送飞书消息失败: {e}")
                return False
        
        # 内容过长，进行分割
        logger.info(f"消息内容超长({len(content)}字符)，将分割成多条发送")
        
        chunks = []
        current_chunk_lines = []
        current_chunk_size = 0
        
        lines = content.split('\n')
        
        for line in lines:
            line_size = len(line) + 1  # 加换行符
            
            if line_size > MAX_CHAR_LENGTH:
                # 单行超长，按字符切分
                if current_chunk_lines:
                    chunks.append("\n".join(current_chunk_lines))
                    current_chunk_lines = []
                    current_chunk_size = 0
                
                char_limit = MAX_CHAR_LENGTH - 10
                for j in range(0, len(line), char_limit):
                    chunks.append(line[j:j+char_limit])
                continue
            
            if current_chunk_size + line_size > MAX_CHAR_LENGTH:
                chunks.append("\n".join(current_chunk_lines))
                current_chunk_lines = [line]
                current_chunk_size = line_size
            else:
                current_chunk_lines.append(line)
                current_chunk_size += line_size
        
        if current_chunk_lines:
            chunks.append("\n".join(current_chunk_lines))
        
        # 发送所有块
        success_count = 0
        total_chunks = len(chunks)
        
        for i, chunk in enumerate(chunks):
            if total_chunks > 1:
                paginated_content = f"({i+1}/{total_chunks})\n{chunk}"
            else:
                paginated_content = chunk
            
            if len(paginated_content) > MAX_CHAR_LENGTH:
                logger.warning(f"分块 {i+1} 加上页码后仍超长，尝试截断")
                paginated_content = paginated_content[:3900] + "\n...(已截断)"
            
            try:
                if self._send_single_feishu_message(paginated_content):
                    success_count += 1
                import time
                time.sleep(0.5)  # 避免触发频率限制
            except Exception as e:
                logger.error(f"发送第 {i+1} 条飞书消息失败: {e}")
        
        return success_count == total_chunks
    
    def _send_single_feishu_message(self, content: str) -> bool:
        """
        发送单条飞书消息
        
        使用富文本卡片格式，支持 Markdown 渲染
        """
        # 飞书富文本卡片格式（支持 Markdown）
        payload = {
            "msg_type": "interactive",
            "card": {
                "config": {
                    "wide_screen_mode": True,
                    "enable_forward": True
                },
                "elements": [
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": content
                        }
                    }
                ]
            }
        }
        
        try:
            response = requests.post(
                self._feishu_webhook_url,
                json=payload,
                timeout=10
            )
            
            if response.status_code == 200:
                result = response.json()
                # 飞书成功返回 {"code": 0} 或 {"StatusCode": 0}
                if result.get('code') == 0 or result.get('StatusCode') == 0:
                    logger.info("飞书消息发送成功")
                    return True
                else:
                    logger.error(f"飞书返回错误: {result}")
                    return False
            else:
                logger.error(f"飞书请求失败: {response.status_code}, {response.text}")
                return False
        except Exception as e:
            logger.error(f"发送飞书消息异常: {e}")
            return False
            

    def generate_summary_image(self, results: List[AnalysisResult]) -> Optional[str]:
        """
        生成汇总分析图片
        
        Args:
            results: 分析结果列表
            
        Returns:
            生成的图片路径，如果失败返回 None
        """
        try:
            import matplotlib.pyplot as plt
            import pandas as pd
            import os
            
            # 设置中文字体（优先使用文泉驿微米黑，这是 GitHub Actions 推荐的中文）
            plt.rcParams['font.sans-serif'] = ['WenQuanYi Micro Hei', 'SimHei', 'Microsoft YaHei', 'Arial Unicode MS', 'Cymzen', 'DejaVu Sans']
            plt.rcParams['axes.unicode_minus'] = False
            
            # 准备数据
            import re
            
            def extract_price(text):
                """从文本中提取价格数字"""
                if not text or text == '-':
                    return '-'
                # 匹配浮点数或整数
                match = re.search(r'\d+(\.\d+)?', str(text))
                return match.group(0) if match else '-'

            data = []
            for r in results:
                # 获取买卖点
                dashboard = r.dashboard if hasattr(r, 'dashboard') else None
                battle = dashboard.get('battle_plan', {}) if dashboard else {}
                sniper = battle.get('sniper_points', {}) if battle else {}
                
                # 仅提取价格数字
                buy_point = extract_price(sniper.get('ideal_buy', '-'))
                sell_point = extract_price(sniper.get('take_profit', '-'))
                stop_loss = extract_price(sniper.get('stop_loss', '-'))
                
                # 简化建议 (移除Emoji，避免方框乱码，依靠颜色区分)
                advice = r.operation_advice
                    
                data.append({
                    '代码': r.code,
                    '名称': r.name,
                    '评分': r.sentiment_score,
                    '建议': advice,
                    '趋势': r.trend_prediction,
                    '买点': buy_point,
                    '卖点': sell_point,
                    '止损': stop_loss
                })
            
            if not data:
                return None
            
            # 创建 DataFrame 并排序
            df = pd.DataFrame(data)
            df = df.sort_values('评分', ascending=False)
            
            # 绘图 Setup
            # 计算图片高度：表头+每一行的高度
            row_height = 0.5
            header_height = 0.8
            fig_height = len(df) * row_height + header_height + 1
            
            fig, ax = plt.subplots(figsize=(12, fig_height))
            ax.axis('off')
            
            # 标题
            date_str = datetime.now().strftime('%Y-%m-%d')
            plt.title(f"A股智能分析汇总 ({date_str})", fontsize=16, pad=20, fontweight='bold', color='#333333')
            
            # 绘制表格
            table = ax.table(
                cellText=df.values,
                colLabels=df.columns,
                cellLoc='center',
                loc='center',
                bbox=[0, 0, 1, 1]
            )
            
            # 美化表格
            table.auto_set_font_size(False)
            table.set_fontsize(11)
            
            # 样式调整
            for (row, col), cell in table.get_celld().items():
                cell.set_edgecolor('#cccccc')
                cell.set_linewidth(0.5)
                
                if row == 0:
                    cell.set_facecolor('#40466e')
                    cell.set_text_props(color='w', weight='bold')
                    cell.set_height(0.08)
                else:
                    cell.set_height(0.06)
                    # 根据“建议”列设置背景色 (index 3 is '建议')
                    if col == 3: 
                        text = str(df.iloc[row-1]['建议'])
                        if '买' in text or '增' in text:
                            cell.set_text_props(color='#2e7d32', weight='bold') # 深绿字
                            cell.set_facecolor('#e8f5e9') # 浅绿底
                        elif '卖' in text or '减' in text:
                            cell.set_text_props(color='#c62828', weight='bold') # 深红字
                            cell.set_facecolor('#ffebee') # 浅红底
                        elif '持' in text or '观' in text:
                            cell.set_text_props(color='#f9a825', weight='bold') # 深黄字
                            cell.set_facecolor('#fffde7') # 浅黄底
            
            # 保存图片
            image_dir = "reports/images"
            if not os.path.exists(image_dir):
                os.makedirs(image_dir)
            
            image_path = f"{image_dir}/summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            plt.savefig(image_path, bbox_inches='tight', dpi=150, pad_inches=0.2)
            plt.close()
            
            logger.info(f"汇总图片生成成功: {image_path}")
            return os.path.abspath(image_path)
            
        except Exception as e:
            logger.error(f"生成汇总图片失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None

    def send_image_to_wechat(self, image_path: str) -> bool:
        """
        发送图片到企业微信
        """
        if not self.is_available():
            return False
            
        import hashlib
        import base64
        
        try:
            with open(image_path, 'rb') as f:
                content = f.read()
                
            # 计算 MD5
            md5 = hashlib.md5(content).hexdigest()
            
            # 计算 Base64
            b64 = base64.b64encode(content).decode('utf-8')
            
            payload = {
                "msgtype": "image",
                "image": {
                    "base64": b64,
                    "md5": md5
                }
            }
            
            logger.info(f"正在推送图片到企业微信: {image_path}")
            
            response = requests.post(
                self._webhook_url,
                json=payload,
                timeout=30
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get('errcode') == 0:
                    logger.info("图片推送成功")
                    return True
                else:
                    logger.error(f"图片推送失败: {result}")
                    return False
            else:
                logger.error(f"请求失败: {response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"发送图片异常: {e}")
            return False
    
    def _send_chunked_messages(self, content: str, max_length: int) -> bool:
        """
        分段发送长消息
        
        按段落（---）分割，确保每段不超过最大长度
        """
        # 按分隔线分割
        sections = content.split("\n---\n")
        
        current_chunk = []
        current_length = 0
        all_success = True
        chunk_index = 1
        
        for section in sections:
            section_with_divider = section + "\n---\n"
            section_length = len(section_with_divider)
            
            if current_length + section_length > max_length:
                # 发送当前块
                if current_chunk:
                    chunk_content = "\n---\n".join(current_chunk)
                    logger.info(f"发送消息块 {chunk_index}...")
                    if not self._send_single_message(chunk_content):
                        all_success = False
                    chunk_index += 1
                
                # 重置
                current_chunk = [section]
                current_length = section_length
            else:
                current_chunk.append(section)
                current_length += section_length
        
        # 发送最后一块
        if current_chunk:
            chunk_content = "\n---\n".join(current_chunk)
            logger.info(f"发送消息块 {chunk_index}（最后）...")
            if not self._send_single_message(chunk_content):
                all_success = False
        
        return all_success
    
    def save_report_to_file(
        self, 
        content: str, 
        filename: Optional[str] = None
    ) -> str:
        """
        保存日报到本地文件
        
        Args:
            content: 日报内容
            filename: 文件名（可选，默认按日期生成）
            
        Returns:
            保存的文件路径
        """
        from pathlib import Path
        
        if filename is None:
            date_str = datetime.now().strftime('%Y%m%d')
            filename = f"report_{date_str}.md"
        
        # 确保 reports 目录存在
        reports_dir = Path(__file__).parent / 'reports'
        reports_dir.mkdir(parents=True, exist_ok=True)
        
        filepath = reports_dir / filename
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        
        logger.info(f"日报已保存到: {filepath}")
        return str(filepath)


class NotificationBuilder:
    """
    通知消息构建器
    
    提供便捷的消息构建方法
    """
    
    @staticmethod
    def build_simple_alert(
        title: str,
        content: str,
        alert_type: str = "info"
    ) -> str:
        """
        构建简单的提醒消息
        
        Args:
            title: 标题
            content: 内容
            alert_type: 类型（info, warning, error, success）
        """
        emoji_map = {
            "info": "ℹ️",
            "warning": "⚠️",
            "error": "❌",
            "success": "✅",
        }
        emoji = emoji_map.get(alert_type, "📢")
        
        return f"{emoji} **{title}**\n\n{content}"
    
    @staticmethod
    def build_stock_summary(results: List[AnalysisResult]) -> str:
        """
        构建股票摘要（简短版）
        
        适用于快速通知
        """
        lines = ["📊 **今日自选股摘要**", ""]
        
        for r in sorted(results, key=lambda x: x.sentiment_score, reverse=True):
            emoji = r.get_emoji()
            lines.append(f"{emoji} {r.name}({r.code}): {r.operation_advice} | 评分 {r.sentiment_score}")
        
        return "\n".join(lines)


# 便捷函数
def get_notification_service() -> NotificationService:
    """获取通知服务实例"""
    return NotificationService()


def send_daily_report(results: List[AnalysisResult]) -> bool:
    """
    发送每日报告的快捷方式
    
    自动生成报告并推送到企业微信
    """
    service = get_notification_service()
    
    # 生成报告
    report = service.generate_daily_report(results)
    
    # 保存到本地
    service.save_report_to_file(report)
    
    # 推送到企业微信
    return service.send_to_wechat(report)


if __name__ == "__main__":
    # 测试代码
    logging.basicConfig(level=logging.DEBUG)
    
    # 模拟分析结果
    test_results = [
        AnalysisResult(
            code='600519',
            name='贵州茅台',
            sentiment_score=75,
            trend_prediction='看多',
            analysis_summary='技术面强势，消息面利好',
            operation_advice='买入',
            technical_analysis='放量突破 MA20，MACD 金叉',
            news_summary='公司发布分红公告，业绩超预期',
        ),
        AnalysisResult(
            code='000001',
            name='平安银行',
            sentiment_score=45,
            trend_prediction='震荡',
            analysis_summary='横盘整理，等待方向',
            operation_advice='持有',
            technical_analysis='均线粘合，成交量萎缩',
            news_summary='近期无重大消息',
        ),
        AnalysisResult(
            code='300750',
            name='宁德时代',
            sentiment_score=35,
            trend_prediction='看空',
            analysis_summary='技术面走弱，注意风险',
            operation_advice='卖出',
            technical_analysis='跌破 MA10 支撑，量能不足',
            news_summary='行业竞争加剧，毛利率承压',
        ),
    ]
    
    service = NotificationService()
    
    # 生成日报
    print("=== 生成日报测试 ===")
    report = service.generate_daily_report(test_results)
    print(report)
    
    # 保存到文件
    print("\n=== 保存日报 ===")
    filepath = service.save_report_to_file(report)
    print(f"保存成功: {filepath}")
    
    # 推送测试（仅当配置了 Webhook 时）
    if service.is_available():
        print("\n=== 推送测试 ===")
        success = service.send_to_wechat(report)
        print(f"推送结果: {'成功' if success else '失败'}")
    else:
        print("\n企业微信 Webhook 未配置，跳过推送测试")
