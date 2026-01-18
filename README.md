# 跨交易所套利机器人

> 本项目 fork 自 [QuantGuy](https://x.com/yourQuantGuy) 的套利程序，并进行了以下改进：
>
> 1. ✅ **SDK 更新**：适配最新 Lighter SDK，更新 requirements.txt，程序可直接运行
> 2. ✅ **StandX 支持**：新增 StandX 交易所支持，使用相同套利逻辑
> 3. ✅ **统一入口**：新增 `arbitrage2.py` 统一入口，支持多交易所切换

## 📝 版本更新日志

### v1.1 (2026-01-18)

**🔧 重要修复**：
- ✅ 修复 EdgeX 订单查询错误：`GetOrdersParams` → `GetActiveOrderParams`
- ✅ 修复 Lighter 紧急平仓失败：添加缺失的 `order_type` 等必需参数
- ✅ 程序中断时现在能够正确执行紧急平仓，避免持仓风险

**🎯 动态阈值优化**：
- ✅ 初始阈值改为使用最大值（保守策略）而非最小值
- ✅ 避免冷启动时因阈值过低导致的盲目交易
- ✅ 在收集足够数据前，只在最优机会时交易
- ✅ 更新日志信息，清晰显示当前使用的阈值策略

**🔄 代码重构**：
- ✅ 合并 `arbitrage.py` 和 `arbitrage2.py`，统一为单一入口
- ✅ 通过 `--exchange` 参数支持多交易所切换
- ✅ 简化项目结构，减少冗余代码

**📚 文档改进**：
- ✅ 添加动态阈值与命令行参数的关系说明
- ✅ 添加参数优先级表格
- ✅ 添加推荐的启动命令示例
- ✅ 添加紧急平仓机制说明
- ✅ 更新所有示例命令，统一使用 `arbitrage.py`

### v1.0 (初始版本)
- 基础套利功能
- 动态阈值系统
- 智能平仓策略

---

## 📋 目录

- [项目简介](#项目简介)
- [功能特性](#功能特性)
  - [核心功能](#核心功能)
  - [高级功能](#高级功能)
- [系统要求](#系统要求)
- [快速开始](#快速开始)
- [使用方法](#使用方法)
- [项目结构](#项目结构)
- [工作原理](#工作原理)
  - [套利流程](#套利流程)
  - [高级功能工作原理](#高级功能工作原理)
- [交易所特性](#交易所特性)
- [注意事项](#注意事项)
  - [最佳实践](#最佳实践)
- [邀请链接](#邀请链接)

---

## 项目简介

本项目是一个加密货币期货跨交易所套利框架，**仅供学习交流使用，不可直接用于生产环境**。实际交易需谨慎评估风险。

### 支持的交易所组合

| Maker 交易所 | Taker 交易所 | 说明 |
|------------|------------|------|
| **EdgeX** | **Lighter** | EdgeX 挂 post-only 限价单（做市），Lighter 执行市价单对冲 |
| **StandX** | **Lighter** | StandX 挂 post-only 限价单（做市），Lighter 执行市价单对冲 |

机器人通过实时监控两个交易所的订单簿，检测价差机会并自动执行套利交易。

---

## 功能特性

### 核心功能
- 🔄 **跨交易所套利**：自动检测并利用交易所间价差
- 📊 **实时订单簿管理**：WebSocket 实时监控订单簿变化
- 📈 **仓位跟踪**：实时跟踪和管理交易仓位
- 🛡️ **风险控制**：支持最大仓位限制和订单超时控制
- 📝 **数据记录**：记录交易数据和统计信息
- ⚡ **异步执行**：基于 asyncio 的高性能异步架构
- 🔌 **模块化设计**：易于扩展新的交易所支持

### 高级功能

#### 🎯 动态阈值调整
- **自适应阈值**：根据历史价差数据自动调整套利触发阈值
- **滑动窗口统计**：使用可配置的滑动窗口收集价差样本
- **百分位数计算**：基于统计百分位数（如 70%）动态设定阈值
- **阈值边界保护**：设置最小/最大阈值防止极端情况
- **定期更新**：可配置的更新间隔（默认 5 分钟）

#### ⏱️ 智能平仓策略
- **宽松平仓条件**：平仓阈值远低于开仓阈值，快速止盈
- **保本平仓**：支持"只要不亏就平仓"策略（MIN_CLOSE_SPREAD=0.0）
- **基于时间的渐进式平仓**：
  - **阶段 1**（1 小时后）：放宽平仓条件，要求一定利润
  - **阶段 2**（2 小时后）：进一步放宽，保本即可平仓
  - **阶段 3**（3 小时后）：强制平仓，允许小额亏损
- **避免长期持仓**：自动降低持仓风险，提高资金周转率

---

## 系统要求

- **Python**：3.8 或更高版本
- **交易所账户**（根据需要）：
  - EdgeX + Lighter 组合：需要 EdgeX 和 Lighter 账户
  - StandX + Lighter 组合：需要 StandX (Solana) 和 Lighter 账户
- **API 密钥**：各交易所的 API 访问权限

---

## 快速开始

### 1. 克隆仓库

```bash
git clone <repository-url>
cd cross-exchange-arbitrage
```

### 2. 创建虚拟环境

```bash
python -m venv venv
```

激活虚拟环境：

**macOS/Linux:**
```bash
source venv/bin/activate
```

**Windows:**
```bash
venv\Scripts\activate
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

### 4. 配置环境变量

复制环境变量模板并填写您的 API 凭证：

```bash
cp env_example.txt .env
```

编辑 `.env` 文件，根据您使用的交易所组合填入相应配置：

#### EdgeX + Lighter 配置

```env
# EdgeX 账户凭证（必需）
EDGEX_ACCOUNT_ID=your_account_id_here
EDGEX_STARK_PRIVATE_KEY=your_stark_private_key_here

# EdgeX API 端点
EDGEX_BASE_URL=https://pro.edgex.exchange
EDGEX_WS_URL=wss://quote.edgex.exchange

# Lighter 配置（必需）
API_KEY_PRIVATE_KEY=your_api_key_private_key_here
LIGHTER_ACCOUNT_INDEX=your_account_index
LIGHTER_API_KEY_INDEX=your_api_key_index
```

#### StandX + Lighter 配置

```env
# StandX 配置（Solana 链）
STANDX_PRIVATE_KEY=你的solana钱包私钥（base58格式）
STANDX_BASE_URL=https://perps.standx.com
STANDX_AUTH_URL=https://api.standx.com

# Lighter 配置（必需）
API_KEY_PRIVATE_KEY=your_api_key_private_key_here
LIGHTER_ACCOUNT_INDEX=your_account_index
LIGHTER_API_KEY_INDEX=your_api_key_index
```

#### 高级功能配置（可选）

##### 动态阈值配置

```env
# 启用动态阈值（根据历史价差自动调整）
USE_DYNAMIC_THRESHOLD=true

# 动态阈值参数
DYNAMIC_THRESHOLD_WINDOW=1000              # 滑动窗口大小（价差观测样本数）
DYNAMIC_THRESHOLD_UPDATE_INTERVAL=300      # 更新间隔（秒，默认 5 分钟）
DYNAMIC_THRESHOLD_MIN=1.0                  # 最小阈值（防止过于激进）
DYNAMIC_THRESHOLD_MAX=10.0                 # 最大阈值（防止过于保守）
DYNAMIC_THRESHOLD_PERCENTILE=0.70          # 使用百分位数（0.70 = 70%）
```

##### 平仓阈值配置

```env
# 平仓时使用更宽松的阈值，实现"只要不亏就平仓"的策略
CLOSE_THRESHOLD_MULTIPLIER=0.1             # 平仓阈值倍数（开仓阈值的 10%）
MIN_CLOSE_SPREAD=0.0                       # 最小平仓价差（0.0 = 保本即可平仓）
```

##### 基于时间的渐进式平仓配置

```env
# 根据持仓时间逐步放宽平仓条件，避免长期持仓
ENABLE_TIME_BASED_CLOSE=true               # 启用基于时间的平仓策略

# 阶段 1：持仓 1 小时后，放宽平仓条件（要求一定利润）
TIME_BASED_CLOSE_STAGE1_HOURS=1.0          # 阶段 1 触发时间
STAGE1_CLOSE_MULTIPLIER=0.2                # 阶段 1 平仓阈值倍数（20%）
STAGE1_MIN_SPREAD=0.3                      # 阶段 1 最小价差（要求 0.3 利润）

# 阶段 2：持仓 2 小时后，进一步放宽（不亏就平）
TIME_BASED_CLOSE_STAGE2_HOURS=2.0          # 阶段 2 触发时间
STAGE2_CLOSE_MULTIPLIER=0.1                # 阶段 2 平仓阈值倍数（10%）
STAGE2_MIN_SPREAD=0.0                      # 阶段 2 最小价差（保本）

# 阶段 3：持仓 3 小时后，强制平仓（允许小亏）
TIME_BASED_CLOSE_STAGE3_HOURS=3.0          # 阶段 3 触发时间
STAGE3_CLOSE_MULTIPLIER=0.05               # 阶段 3 平仓阈值倍数（5%）
STAGE3_MIN_SPREAD=-0.5                     # 阶段 3 最小价差（允许 0.5 亏损）
```

---

## 使用方法

### 基本用法

#### EdgeX 套利

```bash
python arbitrage.py --ticker BTC --size 0.002 --max-position 0.1
# 或明确指定交易所（默认就是 edgex）
python arbitrage.py --exchange edgex --ticker BTC --size 0.002 --max-position 0.1
```

#### StandX 套利

```bash
python arbitrage.py --exchange standx --ticker ETH --size 0.02 --max-position 0.1
```

### 命令行参数

| 参数 | 说明 | 默认值 | 必需 |
|------|------|--------|------|
| `--exchange` | 交易所名称（edgex/standx） | edgex | 否 |
| `--ticker` | 交易对符号 | BTC | 否 |
| `--size` | 每笔订单的交易数量 | - | 是 |
| `--max-position` | 最大持仓限制 | - | 是 |
| `--long-threshold` | 做多套利触发阈值（Lighter 买一价高于 Maker 卖一价的差值） | 10 | 否 |
| `--short-threshold` | 做空套利触发阈值（Maker 买一价高于 Lighter 卖一价的差值） | 10 | 否 |
| `--fill-timeout` | 限价单成交超时时间（秒） | 5 | 否 |

**注意**：当启用动态阈值（`USE_DYNAMIC_THRESHOLD=true`）时，`--long-threshold` 和 `--short-threshold` 参数将被忽略。

### 使用示例

```bash
# EdgeX - 交易 ETH，每笔 0.01 ETH，5 秒超时
python arbitrage.py --exchange edgex --ticker ETH --size 0.01 --max-position 0.1 --fill-timeout 5

# EdgeX - 交易 BTC，最大持仓 0.1 BTC（使用动态阈值，不需要指定 threshold）
python arbitrage.py --ticker BTC --size 0.002 --max-position 0.1

# StandX - 交易 ETH，每笔 0.02 ETH
python arbitrage.py --exchange standx --ticker ETH --size 0.02 --max-position 0.1
```

### ⚠️ 重要：动态阈值与命令行参数的关系

当启用动态阈值功能（`USE_DYNAMIC_THRESHOLD=true`）时，命令行参数 `--long-threshold` 和 `--short-threshold` **将被忽略**。

#### 参数优先级说明

| 配置项 | 动态阈值关闭 | 动态阈值开启 |
|--------|------------|------------|
| `--ticker` | ✅ 生效 | ✅ 生效 |
| `--size` | ✅ 生效 | ✅ 生效 |
| `--max-position` | ✅ 生效 | ✅ 生效 |
| `--long-threshold` | ✅ 生效 | ❌ **被忽略** |
| `--short-threshold` | ✅ 生效 | ❌ **被忽略** |

#### 动态阈值启用时的实际阈值来源

当 `USE_DYNAMIC_THRESHOLD=true` 时，系统使用以下 `.env` 配置：

```env
DYNAMIC_THRESHOLD_MIN=1.0      # 最小阈值（下限保护）
DYNAMIC_THRESHOLD_MAX=10.0     # 最大阈值（初始值，上限保护）
DYNAMIC_THRESHOLD_PERCENTILE=0.70  # 百分位数（70%）
```

**阈值演变过程：**
1. **启动阶段**：使用 `DYNAMIC_THRESHOLD_MAX`（10.0）作为初始阈值（保守策略）
2. **数据收集**：持续收集价差样本，需要至少 100 个样本
3. **首次更新**：收集到 100 个样本后，等待 5 分钟（`DYNAMIC_THRESHOLD_UPDATE_INTERVAL`）进行首次更新
4. **动态调整**：根据历史价差的 70% 百分位数自动调整阈值
5. **边界保护**：阈值始终保持在 `[DYNAMIC_THRESHOLD_MIN, DYNAMIC_THRESHOLD_MAX]` 范围内

#### 推荐的启动命令

**使用动态阈值时（推荐）：**
```bash
# EdgeX - 不需要指定 --long-threshold 和 --short-threshold
python arbitrage.py --ticker ETH --size 0.1 --max-position 0.5

# StandX
python arbitrage.py --exchange standx --ticker ETH --size 0.1 --max-position 0.5
```

**使用固定阈值时：**
```bash
# 在 .env 中设置 USE_DYNAMIC_THRESHOLD=false
# 然后可以使用命令行参数
python arbitrage.py --ticker ETH --size 0.1 --max-position 0.5 --long-threshold 3 --short-threshold 3
```

#### 如何切换模式

**启用动态阈值：**
```env
USE_DYNAMIC_THRESHOLD=true
```

**禁用动态阈值（使用命令行参数）：**
```env
USE_DYNAMIC_THRESHOLD=false
# 或直接删除/注释掉这一行
```

---

## 项目结构

```
cross-exchange-arbitrage/
├── arbitrage.py              # 主程序入口（支持 EdgeX 和 StandX）
├── exchanges/                # 交易所接口实现
│   ├── base.py              # 基础交易所接口（抽象类）
│   ├── edgex.py             # EdgeX 交易所实现
│   ├── standx.py            # StandX 交易所实现（Solana）
│   ├── standx_protocol/     # StandX 协议模块（认证、HTTP客户端）
│   ├── lighter.py           # Lighter 交易所实现
│   └── lighter_custom_websocket.py  # Lighter WebSocket 管理
├── strategy/                 # 交易策略模块
│   ├── edgex_arb.py         # EdgeX 套利策略
│   ├── standx_arb.py        # StandX 套利策略
│   ├── dynamic_threshold.py # 动态阈值计算器
│   ├── order_book_manager.py    # 订单簿管理
│   ├── order_manager.py     # 订单管理
│   ├── position_tracker.py  # 仓位跟踪
│   ├── websocket_manager.py # WebSocket 管理
│   └── data_logger.py       # 数据记录
├── requirements.txt         # Python 依赖
├── env_example.txt          # 环境变量示例
├── .env                     # 环境变量配置（需自行创建）
└── README.md               # 项目说明文档
```

---

## 工作原理

### 套利流程

1. **订单簿监控**：通过 WebSocket 实时接收两个交易所的订单簿更新
2. **价差检测**：计算两个交易所之间的价差
3. **套利机会识别**：当价差超过设定阈值时，识别套利机会
4. **订单执行**：
   - 在 Maker 交易所（EdgeX 或 StandX）上挂 **post-only 限价单**（做市单，赚取手续费返佣）
   - 在 Lighter 上执行 **市价单** 完成对冲
5. **仓位管理**：实时跟踪仓位，确保不超过最大持仓限制
6. **风险控制**：监控订单成交状态，超时未成交则自动取消订单

### 套利逻辑示例

**做多套利（Long Arbitrage）：**
- 条件：Lighter 买一价 > Maker 卖一价 + long_threshold
- 操作：在 Maker 交易所卖出（限价单），在 Lighter 买入（市价单）

**做空套利（Short Arbitrage）：**
- 条件：Maker 买一价 > Lighter 卖一价 + short_threshold
- 操作：在 Maker 交易所买入（限价单），在 Lighter 卖出（市价单）

---

### 高级功能工作原理

#### 🎯 动态阈值机制

传统的固定阈值策略在市场波动变化时可能过于激进或保守。动态阈值通过统计分析自动调整：

1. **数据收集**：持续收集两个交易所之间的价差数据
2. **滑动窗口**：维护最近 N 个价差样本（如 1000 个）
3. **统计计算**：定期（如每 5 分钟）计算价差的百分位数
4. **阈值更新**：将计算出的百分位数值（如 70%）作为新的套利阈值
5. **边界保护**：确保阈值在设定的最小值和最大值之间

**优势**：
- 市场平静时降低阈值，捕获更多机会
- 市场波动时提高阈值，避免虚假信号
- 自动适应不同交易对的特性

**重要改进（v1.1）**：
- ✅ **初始阈值优化**：启动时使用 `DYNAMIC_THRESHOLD_MAX`（最大阈值）而非最小阈值
- ✅ **保守启动策略**：在收集足够数据前，只在最优机会时交易
- ✅ **避免冷启动风险**：防止数据不足时的盲目交易
- ✅ **渐进式调整**：随着数据积累，阈值逐步优化到合理水平

**工作流程**：
```
启动 → 使用最大阈值(10.0) → 收集100+样本 → 等待5分钟 → 计算70%百分位 → 更新阈值 → 持续优化
```

#### ⏱️ 智能平仓策略

传统策略要求平仓时价差反转到相同幅度，可能导致长期持仓。智能平仓策略采用渐进式方法：

**1. 宽松平仓阈值**
- 开仓阈值：10（需要较大价差才开仓）
- 平仓阈值：1（只需小幅价差即可平仓）
- 策略：快速止盈，提高资金周转率

**2. 基于时间的渐进式平仓**

| 持仓时间 | 平仓阈值倍数 | 最小价差要求 | 策略说明 |
|---------|------------|------------|---------|
| < 1 小时 | 10% | 0.0 | 保本即可平仓 |
| 1-2 小时 | 20% | 0.3 | 要求小额利润 |
| 2-3 小时 | 10% | 0.0 | 再次降低到保本 |
| > 3 小时 | 5% | -0.5 | 强制平仓，允许小亏 |

**工作流程**：
1. 开仓后，系统记录开仓时间
2. 每次检查平仓条件时，根据持仓时长选择对应阶段的参数
3. 随着时间推移，平仓条件逐步放宽
4. 避免因价差长期不回归而导致的资金占用

**优势**：
- 减少长期持仓风险
- 提高资金利用效率
- 在市场单边行情时及时止损

---

## 交易所特性

### EdgeX
- **认证方式**：Stark 私钥
- **区块链**：StarkNet
- **优势**：成熟稳定，API 完善
- **费率**：支持 post-only 限价单，可获得 maker 返佣

### StandX
- **认证方式**：Solana 钱包签名（复杂 JSON 签名结构）
- **区块链**：Solana
- **优势**：Solana 生态，低延迟
- **API 特点**：
  - REST API：获取价格、持仓、下单
  - WebSocket：接收订单更新推送
  - 支持限价单和市价单

### Lighter
- **角色**：Taker 交易所（执行市价单对冲）
- **优势**：流动性好，执行速度快
- **SDK**：使用最新版本 Lighter Python SDK

---

## 注意事项

⚠️ **风险提示**：

- ❗ **市场风险**：套利交易存在市场风险，价差可能瞬间消失
- ❗ **测试建议**：建议先在测试环境或小额资金下充分测试
- ❗ **网络延迟**：注意网络延迟和交易所 API 限制可能影响套利效果
- ❗ **资金管理**：定期检查仓位和资金状况，避免过度杠杆
- ❗ **手续费**：计算套利收益时需考虑双边手续费成本
- ❗ **API 限制**：注意各交易所的 API 调用频率限制

### 程序中断与紧急平仓

当程序被中断（Ctrl+C）时，系统会自动执行清理流程：

1. **取消未完成订单**：取消所有挂单
2. **检查持仓**：获取实际持仓情况
3. **紧急平仓**：如果存在未平仓位，自动执行市价单平仓

**重要修复（v1.1）**：
- ✅ 修复了 EdgeX 订单查询错误（`GetOrdersParams` → `GetActiveOrderParams`）
- ✅ 修复了 Lighter 紧急平仓失败问题（缺少 `order_type` 参数）
- ✅ 现在程序中断时能够正确平仓，避免持仓风险

**建议**：
- 程序中断后，手动检查交易所持仓确保已完全平仓
- 如果紧急平仓失败，可以使用 `emergency_close.py` 脚本手动平仓

### 最佳实践

#### 基础配置
1. **小额测试**：先用小额资金测试策略有效性
2. **监控日志**：密切关注程序日志，及时发现异常
3. **参数调优**：根据市场情况调整阈值参数
4. **风险控制**：设置合理的最大持仓限制
5. **网络稳定**：确保网络连接稳定，避免断线导致仓位失控

#### 高级功能使用建议

**动态阈值配置建议**：
- 📊 **滑动窗口大小**：建议设置为 500-2000，太小会过于敏感，太大会反应迟钝
- ⏰ **更新间隔**：建议 3-10 分钟，平衡响应速度和计算开销
- 📈 **百分位数选择**：
  - 保守策略：使用 0.80-0.90（只在较大价差时交易）
  - 平衡策略：使用 0.60-0.75（推荐）
  - 激进策略：使用 0.40-0.60（更多交易机会，但风险更高）
- 🛡️ **阈值边界**：
  - 最小值：建议设为手续费成本的 2-3 倍
  - 最大值：建议设为历史最大价差的 50-70%

**平仓策略配置建议**：
- 💰 **平仓阈值倍数**：建议 0.05-0.2（开仓阈值的 5%-20%）
- 📉 **最小平仓价差**：
  - 保守：设为 0.5（要求一定利润）
  - 平衡：设为 0.0（保本即可）
  - 激进：设为 -0.3（允许小额亏损）

**时间平仓策略建议**：
- ⏱️ **阶段时间设置**：根据交易对流动性调整
  - 高流动性（BTC/ETH）：1h / 2h / 3h
  - 中等流动性：2h / 4h / 6h
  - 低流动性：4h / 8h / 12h
- 🎯 **阶段参数设置**：
  - 阶段 1：要求小额利润（如 0.3-0.5）
  - 阶段 2：保本即可（0.0）
  - 阶段 3：允许小亏（-0.3 到 -0.8）

**组合策略示例**：

```env
# 保守策略（适合新手）
USE_DYNAMIC_THRESHOLD=true
DYNAMIC_THRESHOLD_PERCENTILE=0.80
CLOSE_THRESHOLD_MULTIPLIER=0.15
MIN_CLOSE_SPREAD=0.3
ENABLE_TIME_BASED_CLOSE=true
STAGE3_MIN_SPREAD=-0.3

# 平衡策略（推荐）
USE_DYNAMIC_THRESHOLD=true
DYNAMIC_THRESHOLD_PERCENTILE=0.70
CLOSE_THRESHOLD_MULTIPLIER=0.10
MIN_CLOSE_SPREAD=0.0
ENABLE_TIME_BASED_CLOSE=true
STAGE3_MIN_SPREAD=-0.5

# 激进策略（高风险高收益）
USE_DYNAMIC_THRESHOLD=true
DYNAMIC_THRESHOLD_PERCENTILE=0.50
CLOSE_THRESHOLD_MULTIPLIER=0.05
MIN_CLOSE_SPREAD=-0.2
ENABLE_TIME_BASED_CLOSE=true
STAGE3_MIN_SPREAD=-1.0
```

---

## 邀请链接

使用以下邀请链接注册可获得手续费返佣和积分加成：

### 关注 QuantGuy
**X (Twitter)**: [@yourQuantGuy](https://x.com/yourQuantGuy)

### edgeX
[https://pro.edgex.exchange/referral/QUANT](https://pro.edgex.exchange/referral/QUANT)
- 永久享受 VIP 1 费率
- 额外 10% 手续费返佣
- 10% 额外奖励积分

### Backpack
[https://backpack.exchange/join/quant](https://backpack.exchange/join/quant)
- 35% 手续费返佣

### Paradex
[https://app.paradex.trade/r/quant](https://app.paradex.trade/r/quant)
- 10% 手续费返佣
- 5% 积分加成

### grvt
[https://grvt.io/exchange/sign-up?ref=QUANT](https://grvt.io/exchange/sign-up?ref=QUANT)
- 1.3x 全网最高积分加成
- 30% 手续费返佣

### Extended
[https://app.extended.exchange/join/QUANT](https://app.extended.exchange/join/QUANT)
- 10% 即时手续费减免
- 5% 积分加成

### StandX
[https://standx.com/referral?code=JAAWW](https://standx.com/referral?code=JAAWW)
- 等待 YourQuantGuy 官方邀请链接发布

---

## 依赖说明

主要依赖包括：

| 依赖包 | 用途 |
|--------|------|
| `python-dotenv` | 环境变量管理 |
| `asyncio` | 异步编程支持 |
| `requests` | HTTP 请求 |
| `tenacity` | 重试机制 |
| `websockets` | WebSocket 客户端 |
| `edgex-python-sdk` | EdgeX 官方 SDK（fork 版本，支持 post-only） |
| `lighter-python` | Lighter 交易所 SDK |
| `base58` | Base58 编码（Solana 私钥） |
| `solders` | Solana Python 库（StandX 签名） |

---

## 许可证

请查看 [LICENSE](LICENSE) 文件了解详情。

---

## 贡献

欢迎提交 Issue 和 Pull Request！

如有问题或建议，请通过 GitHub Issue 联系。

---

## 免责声明

本项目仅供学习和研究使用，不构成任何投资建议。使用本项目进行实际交易的风险由使用者自行承担。作者不对任何因使用本项目而产生的损失负责。

---

**English speakers**: Please read [README_EN.md](README_EN.md) for the English version of this documentation.
