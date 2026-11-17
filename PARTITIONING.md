# 混合分区规格入湖与精确文件选择

研究站需要在同一个快照中保留旧日文件和新规格文件。README.md 描述正常 V1；本文件规定新增能力，涉及部分以本文件为准。原四字段记录、逐行谓词格式、来源坐标、查询结果及核账格式不变。运行环境仍为 Python 标准库与 SQLite。

## 登记和写入

在 run.Lake 上新增 register_spec(spec) -> spec_id、set_write_spec(spec_id) -> None。spec 是有序的非空列表，项目为以下两种字典之一：
- {"column":"time","transform":"floor","width":10}
- {"column":"device","transform":"mod","modulus":4}

column 只能为 time 或 device。字典须恰含对应三字段；width 为正整数，modulus 为 2～16 的整数；整数均排除 bool。每列至多有一个 floor 项和一个 mod 项，因此最多四项。未知字段、重复同类项、错误类型或范围抛 ValueError。登记须复制输入；相同有序规格重复登记返回相同非空字符串 ID，不同有序规格 ID 不同；ID 在关闭重开后不变。不得修改已登记规格。未知 ID 的 set_write_spec 抛 NotFound，其他非法参数抛 ValueError；失败不改变默认规格。登记不切换默认规格，两项操作均持久化并保持事务失败回滚。

首次打开仍默认按 time//86400 分区，原调用方式及原回归保持有效。set_write_spec 只影响之后的新接纳，不重写或重解释已发布文件。write 的请求身份仍只取原有有序业务内容：切换规格后以同请求同内容重放，完整验证来源后返回首次回执，不产生另一套文件；异内容或源异常仍按 V1 拒绝。

floor 分区值为 value//width，必须向负无穷取整；mod 为 Python 非负余数 value%modulus。NULL 输入使该列所有分区项均为 JSON null，与任何整数分区值不同。每批按所选规格的有序分区值元组分文件，同一元组一个文件；同文件保留原 ordinal 顺序，文件按该元组首次出现顺序排列。空批仍可接纳，无文件。

新规格文件在 snapshot 返回的 files 项中公开 spec_id 和 partition（与规格项目一一对应的 JSON 列表），其余来源及完整性字段保持原义。snapshot 顶层 specs 字典提供这些文件所引用 spec_id 的完整有序规格副本；快照不得依赖当前默认规格解释文件。既有 V1 清单没有这些字段时，day 仍表示固定日分区；无需改写旧对象或旧快照。初始默认规格产生的文件须继续支持原 day 字段和原接口。可以自行设计内部存储，不要求特定表或对象编码。

批次、含分区元数据的快照、head 与接纳回执共同发布；新规格路由与清单覆盖必须校验。读取必要对象时须核对其每行确实满足清单绑定的全部分区条件；仍检查摘要、长度、来源、行数及行序。不能仅因摘要正确而接纳错分区对象。

## 固定查询和冷对象

create_query 的签名、日半开范围及谓词语法不变，day_start/day_end 始终是 UTC 日而非新分区编号。行入选条件仍为：time 非 NULL 且 day_start*86400 <= time < day_end*86400，并且原 SQL 三值谓词结果为 TRUE。不得改变 FALSE、UNKNOWN、NULL 设备及精确整数汇总口径。

对每个文件，必须且仅当存在一对合法 time/device 值，同时满足该文件全部分区条件、查询日范围且使谓词为 TRUE 时才请求读取该对象。time/device 的合法域为任意精度整数及 NULL，未参与分区的列不受文件额外限制。判定对象是该分区允许的值域，不是对象中实际出现的行；实际行仍须完整校验并逐行过滤。不允许保守多读，也不允许用已读内容、金额、记录 ID 或样本推断分区域。

被排除文件即使离线、丢失或损坏也不得访问；仍可能匹配的必要文件不可用时抛 Unavailable，不发布部分成果，恢复原件后可重试。对象访问沿用 lake.objects.read_trace。完成任务重放仍直接返回固定成果。新默认规格、新批次、调用者修改输入以及关闭重开不得改变旧快照、已创建任务或已封存成果的绑定含义。

reconcile 继续读取绑定快照的全部来源，而非只读查询必要文件。它须能校验同批多个新分区、恢复原 ordinal 并保持原互斥分类和独立汇总；不同设备分区落在同一天不构成重复日错误。首次核账的任一来源不可用仍失败，不能把查询裁剪借给完整来源分母；已经保存的核账仍固定重放。

允许扫描全部元数据、全量重算结果和使用现有事务；不增加并发、重分区搬迁、对象回收或进程终止恢复。

## 可手算的边界

规格 [{"column":"time","transform":"floor","width":10},{"column":"time","transform":"mod","modulus":4}] 下，time=3 与 time=7 同属 partition=[0,3]，time=-1 属于 [-1,3]，NULL time 属于 [null,null]。

只写入 time=3 的该分区文件并使其离线，查询日 [0,1)：
- time>=8 AND time<10：联合域为空，成功得到空成果且不请求该文件。
- time>=7 AND time<10：联合域含 7，必须请求该文件并失败；恢复对象后成功得到空成果，因为文件实际只有 time=3。
- time=3 OR device IS NULL：应按两列的同一联合赋值判断，不能把各叶子的“可能”值直接套入 NOT。
- NOT(time<8 OR device IS NULL)：NULL time 或 NULL device 均不能据此入选。

## 交付与运行

保留原公开接口及全部原测试字节，交付分区生产、文件选择和混合来源校验的联动实现，说明规格绑定及精确判空的表示不变量。原文件只是正常 V1，尚未提供新增登记、混合清单或判空实现。

partition_examples.py 是非答案样例与核验入口：oracle 仅自检参照；verify 运行完整原回归与新增联动样例；demo 实际展示冷文件被排除、必要文件失败、恢复原件及重开。补充原序明细/汇总的独立核账、NULL/NOT/OR、负数、无界列、大整数边界以及规格登记、接纳和成果发布回滚的业务核验；样例不是全部契约。

原 .pointwise-runtime.json 的 version=1、kind=cli、setup（argv 数组列表）、verify/demo（各为 argv 数组）保持格式；完成后将 verify 接通原回归和新增联动核验，将 demo 接通实际混合规格与冷对象演示，可复用 partition_examples.py 的相应入口。

原基线检查：
python3.11 -B -m unittest discover -s tests -v
python3.11 -B run.py verify
python3.11 -B run.py demo --data demo-data

新增入口：
python3.11 -B partition_examples.py oracle
python3.11 -B partition_examples.py verify
python3.11 -B partition_examples.py demo
