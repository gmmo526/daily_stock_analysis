# -*- coding: utf-8 -*-
"""
===================================
A股自选股智能分析系统 - 存储层
===================================

职责：
1. 管理 SQLite 数据库连接（单例模式）
2. 定义 ORM 数据模型
3. 提供数据存取接口
4. 实现智能更新逻辑（断点续传）
"""

import logging
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Any
from pathlib import Path

import pandas as pd
from sqlalchemy import (
    create_engine,
    Column,
    String,
    Float,
    Date,
    DateTime,
    Integer,
    Index,
    UniqueConstraint,
    select,
    and_,
    desc,
    text,
)
from sqlalchemy.orm import (
    declarative_base,
    sessionmaker,
    Session,
)
from sqlalchemy.exc import IntegrityError

from config import get_config

logger = logging.getLogger(__name__)

# SQLAlchemy ORM 基类
Base = declarative_base()


# === 数据模型定义 ===

class StockDaily(Base):
    """
    股票日线数据模型
    
    存储每日行情数据和计算的技术指标
    支持多股票、多日期的唯一约束
    """
    __tablename__ = 'stock_daily'
    
    # 主键
    id = Column(Integer, primary_key=True, autoincrement=True)
    
    # 股票代码（如 600519, 000001）
    code = Column(String(10), nullable=False, index=True)
    
    # 交易日期
    date = Column(Date, nullable=False, index=True)
    
    # OHLC 数据
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    
    # 成交数据
    volume = Column(Float)  # 成交量（股）
    amount = Column(Float)  # 成交额（元）
    pct_chg = Column(Float)  # 涨跌幅（%）
    
    # 技术指标
    ma5 = Column(Float)
    ma10 = Column(Float)
    ma20 = Column(Float)
    ma60 = Column(Float)
    ma120 = Column(Float)
    ma250 = Column(Float)
    volume_ratio = Column(Float)  # 量比
    rsi = Column(Float)
    macd_dif = Column(Float)
    macd_dea = Column(Float)
    macd_hist = Column(Float)
    kdj_k = Column(Float)
    kdj_d = Column(Float)
    kdj_j = Column(Float)
    boll_upper = Column(Float)
    boll_mid = Column(Float)
    boll_lower = Column(Float)
    atr = Column(Float)
    obv = Column(Float)
    cci = Column(Float)
    
    # 数据来源
    data_source = Column(String(50))  # 记录数据来源（如 AkshareFetcher）
    
    # 更新时间
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    
    # 唯一约束：同一股票同一日期只能有一条数据
    __table_args__ = (
        UniqueConstraint('code', 'date', name='uix_code_date'),
        Index('ix_code_date', 'code', 'date'),
    )
    
    def __repr__(self):
        return f"<StockDaily(code={self.code}, date={self.date}, close={self.close})>"
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            'code': self.code,
            'date': self.date,
            'open': self.open,
            'high': self.high,
            'low': self.low,
            'close': self.close,
            'volume': self.volume,
            'amount': self.amount,
            'pct_chg': self.pct_chg,
            'ma5': self.ma5,
            'ma10': self.ma10,
            'ma20': self.ma20,
            'ma60': self.ma60,
            'ma120': self.ma120,
            'ma250': self.ma250,
            'volume_ratio': self.volume_ratio,
            'rsi': self.rsi,
            'macd_dif': self.macd_dif,
            'macd_dea': self.macd_dea,
            'macd_hist': self.macd_hist,
            'kdj_k': self.kdj_k,
            'kdj_d': self.kdj_d,
            'kdj_j': self.kdj_j,
            'boll_upper': self.boll_upper,
            'boll_mid': self.boll_mid,
            'boll_lower': self.boll_lower,
            'atr': self.atr,
            'obv': self.obv,
            'cci': self.cci,
            'data_source': self.data_source,
        }


class Position(Base):
    """
    用户持仓数据模型
    
    记录单一账户的持仓成本与数量，用于个性化分析
    """
    __tablename__ = 'positions'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(10), nullable=False, unique=True, index=True)
    cost_price = Column(Float, nullable=False)
    quantity = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    
    def __repr__(self):
        return f"<Position(code={self.code}, cost={self.cost_price}, qty={self.quantity})>"


class PositionSnapshot(Base):
    """
    每日持仓快照
    
    记录每日持仓状态，用于持仓历史回溯
    """
    __tablename__ = 'position_snapshots'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False, index=True)
    code = Column(String(10), nullable=False, index=True)
    cost_price = Column(Float)
    quantity = Column(Integer)
    close_price = Column(Float)
    market_value = Column(Float)
    profit_loss = Column(Float)
    profit_pct = Column(Float)
    
    __table_args__ = (
        UniqueConstraint('code', 'date', name='uix_snapshot_code_date'),
        Index('ix_snapshot_code_date', 'code', 'date'),
    )
    
    def __repr__(self):
        return f"<PositionSnapshot(code={self.code}, date={self.date}, qty={self.quantity})>"


class TradeLog(Base):
    """
    交易记录（买入/卖出）
    """
    __tablename__ = 'trade_logs'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_date = Column(Date, nullable=False, index=True)
    code = Column(String(10), nullable=False, index=True)
    action = Column(String(10), nullable=False)  # BUY/SELL
    price = Column(Float, nullable=False)
    quantity = Column(Integer, nullable=False)
    amount = Column(Float)
    note = Column(String(200))
    created_at = Column(DateTime, default=datetime.now)
    
    def __repr__(self):
        return f"<TradeLog({self.action} {self.code} qty={self.quantity} price={self.price})>"


class Capital(Base):
    """
    用户资金管理模型
    
    记录总资金与风险配置，用于仓位与风险敞口计算
    """
    __tablename__ = 'capital'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    total_capital = Column(Float, nullable=False)
    risk_per_trade = Column(Float, default=0.02)
    max_position_ratio = Column(Float, default=0.30)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    
    def __repr__(self):
        return f"<Capital(total={self.total_capital}, risk={self.risk_per_trade})>"


class DatabaseManager:
    """
    数据库管理器 - 单例模式
    
    职责：
    1. 管理数据库连接池
    2. 提供 Session 上下文管理
    3. 封装数据存取操作
    """
    
    _instance: Optional['DatabaseManager'] = None
    
    def __new__(cls, *args, **kwargs):
        """单例模式实现"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self, db_url: Optional[str] = None):
        """
        初始化数据库管理器
        
        Args:
            db_url: 数据库连接 URL（可选，默认从配置读取）
        """
        if self._initialized:
            return
        
        if db_url is None:
            config = get_config()
            db_url = config.get_db_url()
        
        # 创建数据库引擎
        self._engine = create_engine(
            db_url,
            echo=False,  # 设为 True 可查看 SQL 语句
            pool_pre_ping=True,  # 连接健康检查
        )
        
        # 创建 Session 工厂
        self._SessionLocal = sessionmaker(
            bind=self._engine,
            autocommit=False,
            autoflush=False,
        )
        
        # 创建所有表
        Base.metadata.create_all(self._engine)
        # 确保历史表与新增列可用（轻量迁移）
        self._ensure_stock_daily_schema()
        
        self._initialized = True
        logger.info(f"数据库初始化完成: {db_url}")
    
    @classmethod
    def get_instance(cls) -> 'DatabaseManager':
        """获取单例实例"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    @classmethod
    def reset_instance(cls) -> None:
        """重置单例（用于测试）"""
        if cls._instance is not None:
            cls._instance._engine.dispose()
            cls._instance = None

    def _ensure_stock_daily_schema(self) -> None:
        """
        确保 stock_daily 表包含新增指标列（SQLite 轻量迁移）
        """
        required_columns = {
            'ma60': 'REAL',
            'ma120': 'REAL',
            'ma250': 'REAL',
            'rsi': 'REAL',
            'macd_dif': 'REAL',
            'macd_dea': 'REAL',
            'macd_hist': 'REAL',
            'kdj_k': 'REAL',
            'kdj_d': 'REAL',
            'kdj_j': 'REAL',
            'boll_upper': 'REAL',
            'boll_mid': 'REAL',
            'boll_lower': 'REAL',
            'atr': 'REAL',
            'obv': 'REAL',
            'cci': 'REAL',
        }
        with self._engine.connect() as conn:
            try:
                result = conn.execute(text("PRAGMA table_info(stock_daily)"))
                existing_cols = {row[1] for row in result.fetchall()}
                missing = [c for c in required_columns if c not in existing_cols]
                for col in missing:
                    col_type = required_columns[col]
                    conn.execute(text(f"ALTER TABLE stock_daily ADD COLUMN {col} {col_type}"))
                if missing:
                    logger.info(f"stock_daily 新增列: {', '.join(missing)}")
            except Exception as e:
                logger.warning(f"检查/更新 stock_daily 表结构失败: {e}")
    
    def get_session(self) -> Session:
        """
        获取数据库 Session
        
        使用示例:
            with db.get_session() as session:
                # 执行查询
                session.commit()  # 如果需要
        """
        session = self._SessionLocal()
        try:
            return session
        except Exception:
            session.close()
            raise
    
    def has_today_data(self, code: str, target_date: Optional[date] = None) -> bool:
        """
        检查是否已有指定日期的数据
        
        用于断点续传逻辑：如果已有数据则跳过网络请求
        
        Args:
            code: 股票代码
            target_date: 目标日期（默认今天）
            
        Returns:
            是否存在数据
        """
        if target_date is None:
            target_date = date.today()
        
        with self.get_session() as session:
            result = session.execute(
                select(StockDaily).where(
                    and_(
                        StockDaily.code == code,
                        StockDaily.date == target_date
                    )
                )
            ).scalar_one_or_none()
            
            return result is not None
    
    def get_latest_data(
        self, 
        code: str, 
        days: int = 2
    ) -> List[StockDaily]:
        """
        获取最近 N 天的数据
        
        用于计算"相比昨日"的变化
        
        Args:
            code: 股票代码
            days: 获取天数
            
        Returns:
            StockDaily 对象列表（按日期降序）
        """
        with self.get_session() as session:
            results = session.execute(
                select(StockDaily)
                .where(StockDaily.code == code)
                .order_by(desc(StockDaily.date))
                .limit(days)
            ).scalars().all()
            
            return list(results)
    
    def get_data_range(
        self, 
        code: str, 
        start_date: date, 
        end_date: date
    ) -> List[StockDaily]:
        """
        获取指定日期范围的数据
        
        Args:
            code: 股票代码
            start_date: 开始日期
            end_date: 结束日期
            
        Returns:
            StockDaily 对象列表
        """
        with self.get_session() as session:
            results = session.execute(
                select(StockDaily)
                .where(
                    and_(
                        StockDaily.code == code,
                        StockDaily.date >= start_date,
                        StockDaily.date <= end_date
                    )
                )
                .order_by(StockDaily.date)
            ).scalars().all()
            
            return list(results)
    
    def save_daily_data(
        self, 
        df: pd.DataFrame, 
        code: str,
        data_source: str = "Unknown"
    ) -> int:
        """
        保存日线数据到数据库
        
        策略：
        - 使用 UPSERT 逻辑（存在则更新，不存在则插入）
        - 跳过已存在的数据，避免重复
        
        Args:
            df: 包含日线数据的 DataFrame
            code: 股票代码
            data_source: 数据来源名称
            
        Returns:
            新增/更新的记录数
        """
        if df is None or df.empty:
            logger.warning(f"保存数据为空，跳过 {code}")
            return 0
        
        saved_count = 0
        
        with self.get_session() as session:
            try:
                for _, row in df.iterrows():
                    # 解析日期
                    row_date = row.get('date')
                    if isinstance(row_date, str):
                        row_date = datetime.strptime(row_date, '%Y-%m-%d').date()
                    elif isinstance(row_date, datetime):
                        row_date = row_date.date()
                    elif isinstance(row_date, pd.Timestamp):
                        row_date = row_date.date()
                    
                    # 检查是否已存在
                    existing = session.execute(
                        select(StockDaily).where(
                            and_(
                                StockDaily.code == code,
                                StockDaily.date == row_date
                            )
                        )
                    ).scalar_one_or_none()
                    
                    if existing:
                        # 更新现有记录
                        existing.open = row.get('open')
                        existing.high = row.get('high')
                        existing.low = row.get('low')
                        existing.close = row.get('close')
                        existing.volume = row.get('volume')
                        existing.amount = row.get('amount')
                        existing.pct_chg = row.get('pct_chg')
                        existing.ma5 = row.get('ma5')
                        existing.ma10 = row.get('ma10')
                        existing.ma20 = row.get('ma20')
                        existing.ma60 = row.get('ma60')
                        existing.ma120 = row.get('ma120')
                        existing.ma250 = row.get('ma250')
                        existing.volume_ratio = row.get('volume_ratio')
                        existing.rsi = row.get('rsi')
                        existing.macd_dif = row.get('macd_dif')
                        existing.macd_dea = row.get('macd_dea')
                        existing.macd_hist = row.get('macd_hist')
                        existing.kdj_k = row.get('kdj_k')
                        existing.kdj_d = row.get('kdj_d')
                        existing.kdj_j = row.get('kdj_j')
                        existing.boll_upper = row.get('boll_upper')
                        existing.boll_mid = row.get('boll_mid')
                        existing.boll_lower = row.get('boll_lower')
                        existing.atr = row.get('atr')
                        existing.obv = row.get('obv')
                        existing.cci = row.get('cci')
                        existing.data_source = data_source
                        existing.updated_at = datetime.now()
                    else:
                        # 创建新记录
                        record = StockDaily(
                            code=code,
                            date=row_date,
                            open=row.get('open'),
                            high=row.get('high'),
                            low=row.get('low'),
                            close=row.get('close'),
                            volume=row.get('volume'),
                            amount=row.get('amount'),
                            pct_chg=row.get('pct_chg'),
                            ma5=row.get('ma5'),
                            ma10=row.get('ma10'),
                            ma20=row.get('ma20'),
                            ma60=row.get('ma60'),
                            ma120=row.get('ma120'),
                            ma250=row.get('ma250'),
                            volume_ratio=row.get('volume_ratio'),
                            rsi=row.get('rsi'),
                            macd_dif=row.get('macd_dif'),
                            macd_dea=row.get('macd_dea'),
                            macd_hist=row.get('macd_hist'),
                            kdj_k=row.get('kdj_k'),
                            kdj_d=row.get('kdj_d'),
                            kdj_j=row.get('kdj_j'),
                            boll_upper=row.get('boll_upper'),
                            boll_mid=row.get('boll_mid'),
                            boll_lower=row.get('boll_lower'),
                            atr=row.get('atr'),
                            obv=row.get('obv'),
                            cci=row.get('cci'),
                            data_source=data_source,
                        )
                        session.add(record)
                        saved_count += 1
                
                session.commit()
                logger.info(f"保存 {code} 数据成功，新增 {saved_count} 条")
                
            except Exception as e:
                session.rollback()
                logger.error(f"保存 {code} 数据失败: {e}")
                raise
        
        return saved_count
    
    def get_analysis_context(
        self, 
        code: str,
        target_date: Optional[date] = None
    ) -> Optional[Dict[str, Any]]:
        """
        获取分析所需的上下文数据
        
        返回今日数据 + 昨日数据的对比信息
        
        Args:
            code: 股票代码
            target_date: 目标日期（默认今天）
            
        Returns:
            包含今日数据、昨日对比等信息的字典
        """
        if target_date is None:
            target_date = date.today()
        
        # 获取最近2天数据
        recent_data = self.get_latest_data(code, days=2)
        
        if not recent_data:
            logger.warning(f"未找到 {code} 的数据")
            return None
        
        today_data = recent_data[0]
        yesterday_data = recent_data[1] if len(recent_data) > 1 else None
        
        context = {
            'code': code,
            'date': today_data.date.isoformat(),
            'today': today_data.to_dict(),
        }
        
        if yesterday_data:
            context['yesterday'] = yesterday_data.to_dict()
            
            # 计算相比昨日的变化
            if yesterday_data.volume and yesterday_data.volume > 0:
                context['volume_change_ratio'] = round(
                    today_data.volume / yesterday_data.volume, 2
                )
            
            if yesterday_data.close and yesterday_data.close > 0:
                context['price_change_ratio'] = round(
                    (today_data.close - yesterday_data.close) / yesterday_data.close * 100, 2
                )
            
            # 均线形态判断
            context['ma_status'] = self._analyze_ma_status(today_data)
        
        # 关联持仓信息（如有）
        position = self.get_position(code)
        capital = self.get_capital()
        if position:
            current_price = today_data.close or 0
            cost_price = position.cost_price or 0
            quantity = position.quantity or 0
            profit_loss = (current_price - cost_price) * quantity
            profit_pct = ((current_price - cost_price) / cost_price * 100) if cost_price > 0 else 0
            position_ratio = None
            risk_exposure = None
            risk_ratio = None
            if capital and capital.total_capital > 0:
                market_value = current_price * quantity
                position_ratio = market_value / capital.total_capital
                # 默认止损位为成本价 -5%
                stop_loss_price = cost_price * 0.95
                risk_exposure = max(cost_price - stop_loss_price, 0) * quantity
                risk_ratio = risk_exposure / capital.total_capital
            context['position'] = {
                'cost_price': cost_price,
                'quantity': quantity,
                'market_value': current_price * quantity,
                'profit_loss': round(profit_loss, 2),
                'profit_pct': round(profit_pct, 2),
                'position_ratio': round(position_ratio, 4) if position_ratio is not None else None,
                'risk_exposure': round(risk_exposure, 2) if risk_exposure is not None else None,
                'risk_ratio': round(risk_ratio, 4) if risk_ratio is not None else None,
            }
            context['is_holding'] = True
        else:
            context['is_holding'] = False
        
        if capital:
            context['capital'] = {
                'total_capital': capital.total_capital,
                'risk_per_trade': capital.risk_per_trade,
                'max_position_ratio': capital.max_position_ratio,
            }
        
        history_summary = self.get_position_history_summary(code)
        if history_summary:
            context['position_history'] = history_summary
        
        return context

    def get_position(self, code: str) -> Optional[Position]:
        """获取指定股票的持仓信息"""
        with self.get_session() as session:
            return session.execute(
                select(Position).where(Position.code == code)
            ).scalar_one_or_none()

    def list_positions(self) -> List[Position]:
        """获取全部持仓列表"""
        with self.get_session() as session:
            return list(
                session.execute(select(Position).order_by(Position.code)).scalars().all()
            )

    def upsert_position(self, code: str, cost_price: float, quantity: int) -> Position:
        """
        新增或更新持仓信息
        
        Args:
            code: 股票代码
            cost_price: 成本价
            quantity: 持仓数量
        """
        with self.get_session() as session:
            position = session.execute(
                select(Position).where(Position.code == code)
            ).scalar_one_or_none()
            
            if position:
                position.cost_price = cost_price
                position.quantity = quantity
                position.updated_at = datetime.now()
            else:
                position = Position(
                    code=code,
                    cost_price=cost_price,
                    quantity=quantity
                )
                session.add(position)
            
            session.commit()
            return position

    def remove_position(self, code: str) -> bool:
        """删除指定股票的持仓信息"""
        with self.get_session() as session:
            position = session.execute(
                select(Position).where(Position.code == code)
            ).scalar_one_or_none()
            
            if not position:
                return False
            
            session.delete(position)
            session.commit()
            return True

    def save_daily_snapshot(self, target_date: Optional[date] = None) -> int:
        """
        保存每日持仓快照（按当日收盘价计算）
        """
        if target_date is None:
            target_date = date.today()
        
        positions = self.list_positions()
        if not positions:
            return 0
        
        saved = 0
        with self.get_session() as session:
            try:
                for pos in positions:
                    latest = self.get_latest_data(pos.code, days=1)
                    close_price = latest[0].close if latest and latest[0].close else pos.cost_price
                    market_value = (pos.quantity or 0) * (close_price or 0)
                    profit_loss = (close_price - pos.cost_price) * pos.quantity if pos.cost_price else 0
                    profit_pct = ((close_price - pos.cost_price) / pos.cost_price * 100) if pos.cost_price else 0
                    
                    existing = session.execute(
                        select(PositionSnapshot).where(
                            and_(
                                PositionSnapshot.code == pos.code,
                                PositionSnapshot.date == target_date
                            )
                        )
                    ).scalar_one_or_none()
                    
                    if existing:
                        existing.cost_price = pos.cost_price
                        existing.quantity = pos.quantity
                        existing.close_price = close_price
                        existing.market_value = market_value
                        existing.profit_loss = profit_loss
                        existing.profit_pct = profit_pct
                    else:
                        snapshot = PositionSnapshot(
                            date=target_date,
                            code=pos.code,
                            cost_price=pos.cost_price,
                            quantity=pos.quantity,
                            close_price=close_price,
                            market_value=market_value,
                            profit_loss=profit_loss,
                            profit_pct=profit_pct,
                        )
                        session.add(snapshot)
                        saved += 1
                
                session.commit()
            except Exception:
                session.rollback()
                raise
        
        return saved

    def list_position_history(self, code: str) -> List[PositionSnapshot]:
        """获取指定股票的持仓历史快照"""
        with self.get_session() as session:
            return list(
                session.execute(
                    select(PositionSnapshot)
                    .where(PositionSnapshot.code == code)
                    .order_by(PositionSnapshot.date)
                ).scalars().all()
            )

    def add_trade_log(
        self,
        trade_date: date,
        code: str,
        action: str,
        price: float,
        quantity: int,
        note: Optional[str] = None
    ) -> TradeLog:
        """新增交易记录"""
        amount = price * quantity
        with self.get_session() as session:
            log = TradeLog(
                trade_date=trade_date,
                code=code,
                action=action,
                price=price,
                quantity=quantity,
                amount=amount,
                note=note
            )
            session.add(log)
            session.commit()
            return log

    def list_trade_logs(self, code: Optional[str] = None) -> List[TradeLog]:
        """查询交易记录"""
        with self.get_session() as session:
            query = select(TradeLog)
            if code:
                query = query.where(TradeLog.code == code)
            query = query.order_by(TradeLog.trade_date.desc(), TradeLog.id.desc())
            return list(session.execute(query).scalars().all())

    def get_position_history_summary(self, code: str) -> Optional[Dict[str, Any]]:
        """
        计算持仓历史摘要（建仓日期、持仓天数、最大浮盈、最大回撤）
        """
        snapshots = self.list_position_history(code)
        if not snapshots:
            return None
        
        profit_pcts = [s.profit_pct for s in snapshots if s.profit_pct is not None]
        if not profit_pcts:
            return None
        
        max_profit = max(profit_pcts)
        # 最大回撤计算（基于盈亏比例序列）
        peak = profit_pcts[0]
        max_drawdown = 0.0
        for pct in profit_pcts:
            peak = max(peak, pct)
            drawdown = peak - pct
            max_drawdown = max(max_drawdown, drawdown)
        
        first_date = snapshots[0].date
        holding_days = (date.today() - first_date).days + 1
        
        return {
            'first_buy_date': first_date.isoformat(),
            'holding_days': holding_days,
            'max_profit_pct': round(max_profit, 2),
            'max_drawdown': round(max_drawdown, 2),
        }

    def get_capital(self) -> Optional[Capital]:
        """获取资金配置"""
        with self.get_session() as session:
            return session.execute(select(Capital)).scalar_one_or_none()

    def set_capital(
        self, 
        total_capital: float, 
        risk_per_trade: float = 0.02, 
        max_position_ratio: float = 0.30
    ) -> Capital:
        """设置或更新资金配置"""
        with self.get_session() as session:
            capital = session.execute(select(Capital)).scalar_one_or_none()
            if capital:
                capital.total_capital = total_capital
                capital.risk_per_trade = risk_per_trade
                capital.max_position_ratio = max_position_ratio
                capital.updated_at = datetime.now()
            else:
                capital = Capital(
                    total_capital=total_capital,
                    risk_per_trade=risk_per_trade,
                    max_position_ratio=max_position_ratio,
                )
                session.add(capital)
            session.commit()
            return capital

    def get_portfolio_summary(self) -> Dict[str, Any]:
        """获取持仓组合摘要（基于最新收盘价）"""
        capital = self.get_capital()
        if not capital:
            return {'error': '未设置总资金'}
        
        positions = self.list_positions()
        total = capital.total_capital
        invested = 0.0
        holdings = []
        
        for pos in positions:
            latest = self.get_latest_data(pos.code, days=1)
            current_price = latest[0].close if latest and latest[0].close else pos.cost_price
            market_value = pos.quantity * current_price
            invested += market_value
            
            stop_loss_price = pos.cost_price * 0.95
            risk_exposure = max(pos.cost_price - stop_loss_price, 0) * pos.quantity
            
            holdings.append({
                'code': pos.code,
                'cost_price': pos.cost_price,
                'quantity': pos.quantity,
                'current_price': current_price,
                'market_value': market_value,
                'position_ratio': market_value / total if total > 0 else 0,
                'profit_pct': (current_price - pos.cost_price) / pos.cost_price * 100 if pos.cost_price > 0 else 0,
                'risk_exposure': risk_exposure,
            })
        
        return {
            'total_capital': total,
            'invested': invested,
            'available': total - invested,
            'invested_ratio': invested / total if total > 0 else 0,
            'holdings': holdings,
            'total_risk_exposure': sum(h['risk_exposure'] for h in holdings),
            'risk_per_trade': capital.risk_per_trade,
            'max_position_ratio': capital.max_position_ratio,
        }
    
    def _analyze_ma_status(self, data: StockDaily) -> str:
        """
        分析均线形态
        
        判断条件：
        - 多头排列：close > ma5 > ma10 > ma20
        - 空头排列：close < ma5 < ma10 < ma20
        - 震荡整理：其他情况
        """
        close = data.close or 0
        ma5 = data.ma5 or 0
        ma10 = data.ma10 or 0
        ma20 = data.ma20 or 0
        
        if close > ma5 > ma10 > ma20 > 0:
            return "多头排列 📈"
        elif close < ma5 < ma10 < ma20 and ma20 > 0:
            return "空头排列 📉"
        elif close > ma5 and ma5 > ma10:
            return "短期向好 🔼"
        elif close < ma5 and ma5 < ma10:
            return "短期走弱 🔽"
        else:
            return "震荡整理 ↔️"


# 便捷函数
def get_db() -> DatabaseManager:
    """获取数据库管理器实例的快捷方式"""
    return DatabaseManager.get_instance()


if __name__ == "__main__":
    # 测试代码
    logging.basicConfig(level=logging.DEBUG)
    
    db = get_db()
    
    print("=== 数据库测试 ===")
    print(f"数据库初始化成功")
    
    # 测试检查今日数据
    has_data = db.has_today_data('600519')
    print(f"茅台今日是否有数据: {has_data}")
    
    # 测试保存数据
    test_df = pd.DataFrame({
        'date': [date.today()],
        'open': [1800.0],
        'high': [1850.0],
        'low': [1780.0],
        'close': [1820.0],
        'volume': [10000000],
        'amount': [18200000000],
        'pct_chg': [1.5],
        'ma5': [1810.0],
        'ma10': [1800.0],
        'ma20': [1790.0],
        'volume_ratio': [1.2],
    })
    
    saved = db.save_daily_data(test_df, '600519', 'TestSource')
    print(f"保存测试数据: {saved} 条")
    
    # 测试获取上下文
    context = db.get_analysis_context('600519')
    print(f"分析上下文: {context}")
