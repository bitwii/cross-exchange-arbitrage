# 性能优化方案

## 问题分析

### 当前瓶颈

1. **主循环 sleep(0.05s)** - 每次循环强制等待50ms，即使没有交易机会
2. **BBO polling** - 主动轮询EdgeX BBO，而不是事件驱动
3. **同步等待** - `await asyncio.wait_for()` 增加延迟
4. **固定阈值** - 无法适应市场波动变化

### 性能目标

- **延迟**: 从检测到机会到下单 < 10ms
- **响应**: 事件驱动，BBO更新立即触发检查
- **吞吐**: 支持高频交易（>20次/秒机会检测）
- **自适应**: 根据市场动态调整阈值

---

## 优化方案 1: 移除主循环 sleep

### 当前代码问题
```python
# 主循环中的sleep (第684, 715, 717行)
await asyncio.sleep(0.05)  # 强制等待50ms
```

**问题**:
- 即使有机会也要等50ms
- Spread窗口如果 <1s，可能错过
- 不必要的延迟

### 优化方案

**A. 移除无交易时的sleep**
```python
# 修改前 (第684行)
else:
    # Already at max long position
    if should_log:
        self.logger.info("...")
    await asyncio.sleep(0.05)  # ❌ 不必要

# 修改后
else:
    # Already at max long position
    if should_log:
        self.logger.info("...")
    # ✅ 直接continue，立即检查下一次机会
```

**B. 只在没有数据时sleep**
```python
# 如果BBO数据无效，短暂等待
if not (ex_best_bid and ex_best_ask and lighter_bid and lighter_ask):
    await asyncio.sleep(0.01)  # 仅10ms
    continue
```

---

## 优化方案 2: 事件驱动的BBO更新

### 当前架构问题
```python
# 主循环主动拉取
while not self.stop_flag:
    ex_best_bid, ex_best_ask = await self.order_manager.fetch_edgex_bbo_prices()
    lighter_bid, lighter_ask = self.order_book_manager.get_lighter_bbo()
    # 检查机会...
```

**问题**:
- 主动polling，即使价格没变化
- WebSocket已有实时数据，但每次还要调用函数
- `await asyncio.wait_for(..., timeout=5.0)` 增加不必要的wrapper

### 优化方案: 事件触发检查

**A. WebSocket回调触发交易检查**
```python
class EdgeXArbitrage:
    def __init__(self, ...):
        # 添加BBO变化回调
        self.check_opportunity_event = asyncio.Event()

    def _setup_callbacks(self):
        # 当EdgeX BBO更新时，触发检查
        def on_edgex_bbo_update():
            self.check_opportunity_event.set()

        # 当Lighter BBO更新时，触发检查
        def on_lighter_bbo_update(data):
            self.check_opportunity_event.set()

        self.order_book_manager.set_bbo_update_callback(
            on_edgex_bbo_update, on_lighter_bbo_update
        )
```

**B. 主循环等待事件**
```python
async def _run_trading_loop(self):
    while not self.stop_flag:
        # 等待BBO更新事件（事件驱动，零延迟）
        try:
            await asyncio.wait_for(
                self.check_opportunity_event.wait(),
                timeout=1.0  # 1秒超时防止卡死
            )
            self.check_opportunity_event.clear()
        except asyncio.TimeoutError:
            # 超时也继续，防止卡死
            pass

        # 直接从cache读取（WebSocket已更新）
        ex_best_bid, ex_best_ask = self.order_book_manager.get_edgex_bbo()
        lighter_bid, lighter_ask = self.order_book_manager.get_lighter_bbo()

        # 检查机会
        if self._check_opportunity(ex_best_bid, ex_best_ask, lighter_bid, lighter_ask):
            await self._execute_trade(...)
```

**优势**:
- ✅ BBO更新立即触发检查（<1ms延迟）
- ✅ 无polling开销
- ✅ 充分利用WebSocket实时性

---

## 优化方案 3: 移除不必要的 await/timeout

### 当前代码
```python
# 第586-593行
try:
    ex_best_bid, ex_best_ask = await asyncio.wait_for(
        self.order_manager.fetch_edgex_bbo_prices(),
        timeout=5.0  # ❌ 5秒timeout太长
    )
except asyncio.TimeoutError:
    await asyncio.sleep(0.5)  # ❌ 又sleep 500ms
```

### 优化方案
```python
# 直接从cache读取（同步，零延迟）
ex_best_bid, ex_best_ask = self.order_book_manager.get_edgex_bbo()

# 如果WebSocket数据不可用，才fallback到REST
if not (ex_best_bid and ex_best_ask):
    # 只在fallback时使用REST API
    ex_best_bid, ex_best_ask = await self.order_manager.fetch_edgex_bbo_prices()
```

---

## 优化方案 4: 动态阈值

### 使用新的 DynamicThresholdCalculator

```python
from strategy.dynamic_threshold import DynamicThresholdCalculator

class EdgeXArbitrage:
    def __init__(self, ...):
        # 添加动态阈值计算器
        self.dynamic_threshold = DynamicThresholdCalculator(
            window_size=1000,        # 保留最近1000个spread观察
            update_interval=60,      # 每60秒更新一次阈值
            min_threshold=Decimal('0.5'),  # 最小阈值0.5
            max_threshold=Decimal('10.0'), # 最大阈值10.0
            percentile=0.75,         # 使用75分位数
            logger=self.logger
        )

        # 是否启用动态阈值（可配置）
        self.use_dynamic_threshold = os.getenv('USE_DYNAMIC_THRESHOLD', 'false').lower() == 'true'

    async def _run_trading_loop(self):
        while not self.stop_flag:
            # 获取BBO
            ex_best_bid, ex_best_ask = self.order_book_manager.get_edgex_bbo()
            lighter_bid, lighter_ask = self.order_book_manager.get_lighter_bbo()

            # 计算当前spread
            long_spread = lighter_bid - ex_best_bid if (lighter_bid and ex_best_bid) else Decimal('0')
            short_spread = ex_best_ask - lighter_ask if (ex_best_ask and lighter_ask) else Decimal('0')

            # 记录spread观察（用于统计）
            self.dynamic_threshold.add_spread_observation(long_spread, short_spread)

            # 获取当前阈值
            if self.use_dynamic_threshold:
                long_threshold, short_threshold = self.dynamic_threshold.get_thresholds()
            else:
                long_threshold, short_threshold = self.long_ex_threshold, self.short_ex_threshold

            # 使用动态阈值判断机会
            long_ex = long_spread > long_threshold
            short_ex = short_spread > short_threshold
```

### 配置示例 (.env)
```bash
# 启用动态阈值
USE_DYNAMIC_THRESHOLD=true

# 如果禁用，使用固定阈值
LONG_EDGEX_THRESHOLD=3
SHORT_EDGEX_THRESHOLD=3
```

---

## 预期性能提升

### 延迟优化
| 指标 | 当前 | 优化后 | 提升 |
|-----|------|--------|------|
| 主循环延迟 | 50ms (sleep) | 0ms (事件驱动) | **-50ms** |
| BBO获取 | 5-10ms (polling) | <1ms (cache) | **-5ms** |
| 机会检测到下单 | ~60ms | <10ms | **-50ms** |
| 每秒检测次数 | ~20次 | 无限制 (事件驱动) | **10x+** |

### 成交率提升（理论）
- **当前**: 50ms延迟可能错过 <1s的spread窗口
- **优化后**: <10ms延迟，可以捕获更多短暂机会
- **预期**: 成交机会增加 20-50%

### 自适应优势
- **固定阈值**: 市场波动时，要么错过机会（阈值太高），要么频繁无效交易（阈值太低）
- **动态阈值**: 自动适应市场波动，始终捕获有价值的机会
- **预期**: 减少30-50%无效信号

---

## 实施计划

### Phase 1: 移除不必要的sleep（快速修复）
- [ ] 修改 edgex_arb.py 第684, 715, 717行
- [ ] 移除position limit时的sleep
- [ ] 测试性能

### Phase 2: 优化BBO获取（中等难度）
- [ ] 修改 fetch_edgex_bbo_prices() 逻辑
- [ ] 优先使用WebSocket cache
- [ ] 减少不必要的await/timeout

### Phase 3: 事件驱动架构（高难度）
- [ ] 添加 BBO update callbacks
- [ ] 实现 asyncio.Event 触发机制
- [ ] 重构主循环为事件驱动
- [ ] 完整测试

### Phase 4: 动态阈值（新功能）
- [ ] 集成 DynamicThresholdCalculator
- [ ] 添加环境变量配置
- [ ] 监控和日志
- [ ] A/B测试对比

---

## 风险评估

### 低风险（Phase 1-2）
- ✅ 移除sleep不影响逻辑正确性
- ✅ 优化BBO获取是性能提升，不改变行为
- ⚠️ 需要测试高频场景下的CPU占用

### 中风险（Phase 3）
- ⚠️ 事件驱动架构改动较大
- ⚠️ 需要仔细测试edge cases
- ⚠️ 确保event.clear()不会丢失信号

### 可控风险（Phase 4）
- ✅ 可通过环境变量开关
- ✅ 有min/max安全边界
- ⚠️ 需要足够历史数据才生效（100+ samples）

---

## 监控指标

建议添加以下性能指标：

```python
# 延迟指标
- opportunity_detection_latency_ms  # 从BBO更新到检测机会的延迟
- order_placement_latency_ms        # 从检测到下单的延迟

# 吞吐指标
- bbo_updates_per_second            # 每秒BBO更新次数
- opportunity_checks_per_second     # 每秒机会检查次数

# 动态阈值指标
- long_threshold_value              # 当前long阈值
- short_threshold_value             # 当前short阈值
- spread_mean / spread_std          # Spread均值和标准差
```

---

## 总结

这个优化方案将显著降低延迟（50ms → <10ms），提高吞吐量（20次/秒 → 事件驱动），并通过动态阈值适应市场变化。

建议先实施 Phase 1-2（低风险，快速见效），然后根据效果决定是否进行 Phase 3-4。
