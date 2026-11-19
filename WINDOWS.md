# 双窗复测计划

白班分别核定两个窗口的档位能力，并确认在中间交班点允许哪些有向换档。夜班持两站的固定材料，核对同一场复测在两个窗口能否连续执行。原单窗流程见 README.md。

## Python 调用

沿用 `run_station.open_station`、`open_center` 及其初始化入口。站端继续用 `register` 登记窗口批次，用 `revise` 保存每个档位的测量修订。同一窗口的多个候选档位可以用不同修订表示。写入口自行管理事务，调用时连接须处于空闲事务状态。

| 模块 | 调用 | 返回 |
| --- | --- | --- |
| `issue.publish` | `seal_windows(db, window_ids, allowed_pairs)` | 不透明字符串件号 |
| `issue.transfer` | `get_windows_packet(db, packet_id)` | 按件号领取的固定 V2 JSON 文本 |
| `review.admission` | `admit_windows(db, material)` | 不透明字符串接纳号，`material` 为 JSON 文本或 UTF-8 字节 |
| `review.reconcile` | `review_windows(db, supply_id, receive_id)` | 新计划字典，包含回执号 |
| `reports.readback` | `get_window_plan(db, plan_id)` | 按号读取的原计划字典，包含原回执号 |

### 站端封发和中心接纳

`window_ids` 是含两个互异批号的列表或元组，顺序为前窗、后窗。`allowed_pairs` 是非空列表或元组，每项是含两个修订号的有序列表或元组。所有修订须属于同一站别，两端分别属于对应窗口，每端独立达到本窗的全批量。重复的同一修订对表示同一项许可。两窗的口身份集合相同，清册顺序和正的全批量可分别登记。换档许可由本次封发输入确定，发布新许可表取得新件，旧件按原号仍可领取。

V2 件为 JSON 对象，外层包含整数 `version`（值为 2）、字符串 `packet_id`、`side`（`supply` 或 `receive`）、有序双元素数组 `window_ids` 和 `totals`、四口数组 `ports` 以及对象 `evidence`。外层字段唯一。升数沿用 V1 约分后的有理数字符串。能力坐标按件中 `ports` 的口身份解释。联合证据的内部编码由实现定义。固定件据本次许可及其确切修订生成，保全全部且仅有获准换档所支持的两窗分流。私有阀位标识、站内节点、流路和逐段原测留站。中心接纳可信站端的固定件，拒绝结构、引用或数值非法的材料，以及 V1 材料。中心凭接纳存值独立核账。

### 核账、签发与领取

`review_windows` 按供给、接收顺序取两件已接纳材料。对应窗口的批号、全量和口集必须一致。合法可行时，两站各有一条许可共同支撑整个计划。对于选定许可，两个窗口分别按原满批口径核定，每窗两个站共用同一向量。每个向量的四个量非负、合计等于该窗全量，并满足两份对应修订的全部能力约束。允许签任何一组符合这些条件的向量。合法无解也签发。

计划字典的字段如下。

| 字段 | 值 |
| --- | --- |
| `plan_id`、`receipt_id` | 不透明字符串编号 |
| `supply_id`、`receive_id` | 实际选用的两个 V2 接纳号 |
| `supply_packet_id`、`receive_packet_id` | 两份材料的原件号 |
| `window_ids`、`totals` | 按前后窗顺序的二元组，`totals` 元素为 `Fraction` |
| `ports` | 按口身份字符串排序的四元组 |
| `status` | `feasible` 或 `infeasible` |
| `witness` | 可行时为按窗序排列的两个四元组，各口量为 `Fraction` 并按 `ports` 排列。不可行时为 `None` |

计划、两件出处及回执同次提交。源库更正和中心重开后，按计划号读取已存结果和回执。非法输入、无效许可、满批不足、坏件、混版或选件不符报 `ValueError`。格式有效但找不到的批号、修订号、件号、接纳号或计划号报 `KeyError`。封发、接纳和签发各自原子提交，SQLite 写入或提交失败传播原异常，本次无新增并可重试。保留 V1 接口、编码、已存成果和全部原回归。

## 样例和运行

`samples/windows.json` 给出站别口序、两个窗口的批次与全量、每个候选档位的口容量和有向许可表。`window_fixture.load_cases()` 载入样例，`window_fixture.seed(db, case, side)` 将输入通过 V1 入口登记到空站库，返回两个按档位标签索引的修订号字典。用相应窗口的字典把许可表中的前后标签换成真实修订号，再封发双窗件。

- `transition` 两窗各为 1 L。供给 `original` 对接收 `ab` 签 `infeasible`。改用供给 `corrected` 时，前后窗按 A、B、C、D 顺序为 `(1, 0, 0, 0)` 和 `(0, 1, 0, 0)`。`corrected` 对 `aa` 两窗都只走 A，对 `ba` 仍签 `infeasible`。
- `fractional` 前窗 1 L、后窗 2 L，接收站清册逆序。前窗可签 `(a, 1-a, 0, 0)`，其中 `1/4 ≤ a ≤ 3/4`，后窗固定 `(0, 2, 0, 0)`。
- `python3 -B window_fixture.py` 用已有业务入口打印单窗对照，检查失败时以非零状态退出。

运行平台从 `.pointwise-runtime.json` 调用项目入口。清单保留 `version: 1` 和 `kind: cli`，`setup` 为命令参数数组列表，`verify` 与 `demo` 各为一条命令的参数数组。将双窗业务核验和演示接入清单。核验覆盖原回归并保留失败退出码。演示运行实际封发、接纳和签读，展示同样的窗内能力因许可更正得到不同计划，以及中心重开后按号领取。
