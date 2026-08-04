# Flint 规格参考（ECharts 后端）

> 对应 `flint-chart@0.4.1` 的 ECharts 后端。图型表与语义类型表**由该版本的导出数据直接生成**
> （生成命令见文末），升级钉版时重跑即可、不会与实现漂移。
> 用法与自校验清单见 [`../SKILL.md`](../SKILL.md)。
>
> **哪些是 flint 的、哪些是宿主的**：第 1–5 节描述 flint 本身的规格，跨宿主一致。
> 第 6 节里凡涉及**行数上限、块体积、画布钳制、「渲染失败」/「数据过大」徽标、拒绝
> `data.url`** 的条目，都是**渲染这个块的宿主**定的，不是 flint 的规格——具体数字来自一个
> 内嵌 flint 的宿主实测。换到别的宿主时，这些阈值要按那边的渲染端重新确认；
> 「先聚合、数据内联、不赌冷门图型」这些做法则在哪都成立。

## 1. 顶层字段

```jsonc
{
  "data":   { "values": [ { "列名": 标量, … }, … ] },   // 必填；对象数组、内联、单元格只放标量
  "semantic_types": { "列名": "语义类型" },              // 可选；省略则由 flint 推断
  "chart_spec": {
    "chartType": "Bar Chart",                          // 必填；大小写敏感，逐字符照抄第 2 节
    "encodings": { "x": {"field":"列名"}, "y": {"field":"列名"} },   // 必填
    "baseSize": { "width": 420, "height": 260 },       // 可选；**基准不是上限**，flint 会按类别/分面放大
    "canvasSize": { "width": 900, "height": 700 },     // 可选；**硬上限**（实测生效），类别多时用它兜住
    "chartProperties": { … }                           // 可选；后端相关，本技能用不到
  },
  "options": { … },                                    // 可选；装配选项，本技能用不到
  "field_display_names": { "列名": "图上显示的名字" }     // 可选
}
```

顶层就这 5 个键（`data` / `semantic_types` / `chart_spec` / `options` /
`field_display_names`），与 `ChartAssemblyInput` 一致——`SKILL.md` 第 4 步的清单按这份来。
`options` 与 `chart_spec.chartProperties` 列在这里只是为了让「合法顶层键」这条自校验有据可依；
生成图表时不需要写它们。

- `encodings.<通道>` 可写成 `{"field":"列名"}` 或简写 `"列名"`；另可带
  `type`（`quantitative`/`nominal`/`ordinal`/`temporal`）、`sortOrder`、`sortBy`、`scheme`。
- **不写** ECharts/Vega 原生配置（会被丢弃）、**不写**自定义字段（不会被读取）。
- `data` 在 flint 的类型里也接受 `{ "url": … }`，但**本技能一律用 `values` 内联**：
  图要能只靠这段 markdown 重建，且宿主渲染端通常直接拒绝 `url` 形态（见第 6 节）。

## 2. 图型 × 编码通道（37 种，按用途分组）

**用法**：先按「你想让读者看出什么」挑组，再在组内挑最朴素的那个。通道列的顺序即主次——
前两个通常是必给的，后面的（`color`/`column`/`row`/`opacity`）是可选的分组与分面。

### 对比（类别之间比大小）

| chartType | 通道 | 数据形状要点 |
|---|---|---|
| `Bar Chart` | x/y/color/opacity/column/row | x 类别、y 数值。**默认首选** |
| `Grouped Bar Chart` | x/y/group/color/column/row | 多了 `group` 做簇内分组 |
| `Stacked Bar Chart` | x/y/color/column/row | 看总量兼看构成时用 |
| `Lollipop Chart` | x/y/color/column/row | 类别多、条形太密时的替代 |
| `Pyramid Chart` | x/y/color | 两侧对称对比（如两组人群） |
| `Waterfall Chart` | x/y/color/column/row | 增减累积到期末余额 |
| `Bullet Chart` | y/x/goal/color/column/row | 实际值对目标值（`goal` 列必给） |
| `Gauge Chart` | size/column | 单个进度/占比值 |
| `Radar Chart` | x/y/color/column/row | 多维度打分对比（维度 ≤ 8 才好读） |
| `Rose Chart` | x/y/color/column/row | 南丁格尔玫瑰；类别有周期性时 |

### 趋势（随时间或次序变化）

| chartType | 通道 | 数据形状要点 |
|---|---|---|
| `Line Chart` | x/y/color/opacity/column/row | **默认首选**；x 为时间或有序类别 |
| `Area Chart` | x/y/color/opacity/column/row | 强调累积量 |
| `Streamgraph` | x/y/color/column/row | 多序列构成随时间演变 |
| `Range Area Chart` | x/y/y2/color/column/row | 区间带（`y`/`y2` 两列，如 P50/P95） |
| `Bump Chart` | x/y/color/detail/column/row | 名次随时间变化 |
| `Slope Chart` | x/y/color/detail/column/row | 只有前后两个时点的变化 |
| `Candlestick Chart` | x/open/high/low/close/column/row | 四列 OHLC 必须齐 |
| `Calendar Heatmap` | x/color | **`x` 必须是日期串**（配 `semantic_types` 标 `Date`） |

### 分布（一列数值长什么样）

| chartType | 通道 | 数据形状要点 |
|---|---|---|
| `Histogram` | x/color/column/row | **`x` 必须是数值列**（喂类别列 → 空图） |
| `Density Plot` | x/color/column/row | 同上；平滑版 |
| `ECDF Plot` | x/color/detail/column/row | 同上；累积分布 |
| `Boxplot` | x/y/color/opacity/column/row | x 类别、y 数值（每类多行） |
| `Strip Plot` | x/y/color/size/column/row | 点少时比箱线图更诚实 |
| `Ranged Dot Plot` | x/y/color | 每类一个区间 |

### 相关与矩阵（两列以上数值之间的关系）

| chartType | 通道 | 数据形状要点 |
|---|---|---|
| `Scatter Plot` | x/y/color/size/opacity/column/row | **默认首选**；x/y 都是数值 |
| `Regression` | x/y/size/color/column/row | 散点 + 拟合线 |
| `Connected Scatter Plot` | x/y/order/color/detail/column/row | 按 `order` 连线（轨迹） |
| `Heatmap` | x/y/color/column/row | 两个类别维度 × 一个数值 |
| `Parallel Coordinates` | color/detail | 多个数值列自动成轴；`detail` 标识每条线 |

### 构成与层级

| chartType | 通道 | 数据形状要点 |
|---|---|---|
| `Pie Chart` | size/color/column/row | 类别 ≤ 6 才好读，否则用 Bar |
| `Funnel Chart` | y/size | 有序漏斗阶段 |
| `Treemap` | color/size/detail | 层级由数据列体现；面积=`size` |
| `Sunburst Chart` | color/size/detail/group | 同上，环形。**`color` 必给**——缺了 flint 不装配成 ECharts 图（静默回吐中间态） |
| `Tree` | color/detail/size | 纯结构树 |

### 流向与网络

| chartType | 通道 | 数据形状要点 |
|---|---|---|
| `Sankey Diagram` | x/y/size | 源 / 目标 / 流量三列 |
| `Network Graph` | x/y/size | 节点关系；不适合稠密图 |

### 时间安排

| chartType | 通道 | 数据形状要点 |
|---|---|---|
| `Gantt Chart` | y/x/x2/color/detail/column/row | **`x`/`x2` 是起止两个日期列**，`y` 是任务名 |

## 3. 编码通道的含义

| 通道 | 含义 |
|---|---|
| `x` / `y` | 主坐标轴 |
| `x2` / `y2` | 区间的另一端（Gantt 的结束、Range Area 的上界） |
| `color` | 按类别着色（同时生成图例）；也可按数值做连续色 |
| `size` | 点/块大小、扇区占比 |
| `opacity` | 透明度（次要维度，慎用） |
| `column` / `row` | **分面**：按该列切成一排/一列小图 |
| `group` | 簇内分组（Grouped Bar / Sunburst） |
| `detail` | 标识每条线/每个节点，但不着色 |
| `order` | 连线次序（Connected Scatter） |
| `open`/`high`/`low`/`close` | K 线四列 |
| `goal` | 目标值（Bullet） |

## 4. 语义类型（`semantic_types`，可省）

省略时 flint 会自行推断，够用。想让刻度/格式/排序更贴合语义时才写。**常用的十来个**：

| 类型 | 用在 |
|---|---|
| `Count` / `Quantity` / `Amount` | 计数 / 一般数量 / 金额量 |
| `Percentage` / `PercentageChange` | 百分比 / 同环比 |
| `Score` / `Rank` | 评测得分 / 名次（Rank 会倒序） |
| `Duration` | 时长（延迟、耗时） |
| `Price` | 价格/成本 |
| `Year` / `Quarter` / `Month` / `YearMonth` / `Date` / `DateTime` | 时间粒度 |
| `Category` / `Name` / `Status` | 类别 / 名称 / 状态 |

> **注意**：标了时间类语义类型会把该轴推成时间轴并自动算 domain——`{"年份":"Year"}` 会让 x 轴
> 变成 `temporal` 而不是等距类别。想要等距类别就**别标**，或标 `Category`。

完整枚举（45 个，其余按需查）：
`DateTime Date Time Timestamp Year Quarter Month Week Day Hour YearMonth YearQuarter YearWeek Decade
Duration Quantity Count Amount Price Percentage Temperature Profit PercentageChange Sentiment
Correlation Rank ID Score Latitude Longitude Country State City Region Address ZipCode Category Name
Status Boolean Direction Range Number Unknown`

## 5. 示例

**对比**（最常用形态）：

```flint
{"data":{"values":[{"模型":"model-a","评测得分":72.4},{"模型":"model-b","评测得分":81.9},
                   {"模型":"model-c","评测得分":88.3}]},
 "semantic_types":{"模型":"Category","评测得分":"Score"},
 "chart_spec":{"chartType":"Bar Chart",
               "encodings":{"x":{"field":"模型"},"y":{"field":"评测得分"}},
               "baseSize":{"width":420,"height":260}}}
```

**趋势 + 分组**（多序列随时间）：

```flint
{"data":{"values":[{"月份":"2026-03","模型":"model-a","日均调用":1.2},
                   {"月份":"2026-03","模型":"model-b","日均调用":0.7},
                   {"月份":"2026-04","模型":"model-a","日均调用":1.9},
                   {"月份":"2026-04","模型":"model-b","日均调用":1.1}]},
 "semantic_types":{"月份":"YearMonth","日均调用":"Quantity","模型":"Category"},
 "chart_spec":{"chartType":"Line Chart",
               "encodings":{"x":{"field":"月份"},"y":{"field":"日均调用"},"color":{"field":"模型"}},
               "baseSize":{"width":520,"height":280}}}
```

**分布**（注意 `x` 是数值列）：

```flint
{"data":{"values":[{"延迟毫秒":180},{"延迟毫秒":220},{"延迟毫秒":195},{"延迟毫秒":410},
                   {"延迟毫秒":205},{"延迟毫秒":230},{"延迟毫秒":198},{"延迟毫秒":265}]},
 "semantic_types":{"延迟毫秒":"Duration"},
 "chart_spec":{"chartType":"Histogram","encodings":{"x":{"field":"延迟毫秒"}},
               "baseSize":{"width":420,"height":260}}}
```

## 6. 失败模式对照（实测）

> 本节混着两类成因：**flint 侧**（图型名大小写、通道缺失、语义类型改变轴类型）跨宿主一致；
> **宿主渲染端侧**（各种徽标、行数与体积上限、拒绝 `data.url`）是承载这个块的宿主定的。
> 下面标了「宿主」的行，阈值请按你自己的渲染端确认。

| 症状 | 成因 | 怎么改 |
|---|---|---|
| **（宿主）** 块原样显示成 JSON、带「渲染失败」徽标 | JSON 语法错 / 顶层键非法 / `chartType` 拼错 | 先 parse；再逐字符对照第 2 节的图型名（**大小写敏感**） |
| 抛 `Unknown ECharts chart type: X` | 图型名大小写或空格不对（`Bar chart` ≠ `Bar Chart`） | 照抄第 2 节 |
| **画出来是空的 / 被判为空图** | ①`encodings.*.field` 写了不存在的列；②数据形状与图型不匹配 | ①核对列名（任一行有即可）；②见第 2 节「数据形状要点」——`Histogram`/`Density Plot`/`ECDF Plot` 的 `x` 要数值列，`Calendar Heatmap` 的 `x` 要日期串，`Gantt Chart` 要起止两个日期列 |
| **（宿主）** 带「数据过大」徽标 | 行数 > 1000 / 块体积过大 / **放大后的**画布越界或非有限数（**这三个阈值都是宿主定的**，别处未必是 1000） | 先聚合再画。注意渲染端卡的是 flint **算出来**的尺寸而非你写的 `baseSize`（实测 `420×260` 会长到 `657×514`）——类别太多时减类别，或用 `canvasSize` 设硬上限 |
| JSON 没错、图型名也没错，仍带「渲染失败」徽标 | **图型 × 通道组合没被 ECharts 后端装配**——flint 静默回吐它自己的中间态（`{mark, encoding}`，不是 ECharts 图），实测 `Sunburst Chart` 缺 `color` 即如此 | 补齐该图型「必给」的通道（见第 2 节备注）；拿不准就退回 `Bar Chart` |
| **（宿主）** 图没出现、也没有徽标 | 写了 `data.url` 或把数据放在块外。flint 的类型接受 `data.url`，但渲染端通常拒绝取远端数据 | 数据必须内联进 `data.values` |
| 时间轴变成不想要的等距/非等距 | 时间类 `semantic_types` 改变了轴类型 | 想要等距类别就别标时间类型，或标 `Category` |
| 自定义字段（来源、备注）不见了 | 规格只认第 1 节那几个顶层键 | 写在块外正文 |

## 7. 重新生成本文的表

图型表与语义类型表来自 flint 的导出数据，**不要手抄**——钉版升级后重跑：

```bash
# 图型 × 通道
node -e "import('flint-chart/echarts').then(m=>m.ecAllTemplateDefs.forEach(d=>
  console.log(d.chart+'  ←  '+d.channels.join('/'))))"

# 语义类型枚举
node -e "import('flint-chart/core').then(m=>console.log(Object.keys(m.SemanticTypes).join(' ')))"
```

两条命令都从 npm 上的 `flint-chart` 包读取——`npx --package=flint-chart@<版本> node -e "…"`
即可，无需先装进项目。若你的宿主已经把 flint 随包 vendored（很多内嵌渲染的宿主会这么做），
把 `import('flint-chart/echarts')` 换成指向那份 vendored 副本的路径即可，导出名相同。
