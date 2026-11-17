# 岚湾研究站观测日批湖

Python 3.11 标准库与 SQLite 本地库。采集员接纳离线有序整数观测，复核员冻结日结查询，交付员导出固定明细和逐设备摘要，日结人员独立核对完整来源。本项目没有自动灌数；init 保留已有状态和用户文件。

## 运行

```sh
python3.11 -B run.py init --data station-data
python3.11 -B run.py verify
python3.11 -B -m unittest discover -s tests -v
python3.11 -B run.py demo --data demo-data
```

verify 和原套件使用测试临时目录。demo 在指定目录写入实际对象、目录数据库和 delivery.json，关闭重开后执行查询与核账，再打印实际结果。run-manifest.json 配置演示样例、请求身份及输出文件名。重复 demo 使用相同固定请求，返回首次接纳及交付；不会重写后来编辑的导出目标。修改 samples.json 后重用旧请求会发生身份冲突；另一份接纳应使用新请求 ID 或新数据目录。init 不恢复被修改的对象。

## 一天的工作

1. 数据工程师打开指定目录，首次建立空快照；重开保留旧状态。
2. 采集员交付请求 A 的九行观测，完整验证并记录原行号和有序内容身份。
3. 归档按 UTC 固定日及 NULL 时间分成四个不可变对象，封存后原子发布快照和回执。
4. 复核员在 A 的快照创建日范围 [0,1)、设备 7 或 9 的查询，固定输入。
5. 采集员接纳 B：设备 7 金额 1000；当前快照改变，原任务保持 A 的输入。
6. 原任务读取必要日对象，恢复来源顺序，筛选四行并精确核账，封存交付成果。
7. 交付员以新导出请求完整替换目标，取得绑定内容摘要的回执。
8. 日结人员读取 A 全部来源解释未入选原因，保存独立核账；重开读取固定成果。

A 的三元组为 (time 秒,device,amount 最小计价单位)：a01=(-1,7,2)，a02=(0,7,NULL)，a03=(1,7,5)，a04=(86399,7,-5)，a05=(2,NULL,NULL)，a06=(3,9,NULL)，a07=(86400,7,100)，a08=(NULL,7,11)，a09=(4,NULL,99)。

手算：日 -1 一行、日 0 六行、日 1 一行、NULL 时间一行。指定查询入选 a02、a03、a04、a06；设备 7 摘要为 (3,2,0)，设备 9 为 (1,0,NULL)，三项分别是笔数、非 NULL 金额数、精确和。完整来源 9 = 入选 4 + NULL 时间 1 + 范围外 2 + FALSE 0 + UNKNOWN 2；入选 4 = 金额 NULL 2 + 金额非 NULL 2。B 不进入旧成果。

## 公开 Python API

```python
from run import Lake, NotFound, Unavailable

with Lake('station-data') as lake:
    receipt = lake.write('request-1', iter([
        {'id': 'reading', 'time': 0, 'device': 7, 'amount': 5}
    ]))
    snap = lake.snapshot(receipt['snapshot_id'])
    job = lake.create_query('query-1', snap['id'], 0, 1,
                            ['not', ['is_null', 'device']])
    result_id = lake.run_query(job)
    result = lake.read_result(result_id)
    export_receipt = lake.export_result('export-1', result_id, 'delivery.json')
    audit_id = lake.reconcile('audit-1', result_id)
    accounting = lake.read_result(audit_id)
```

Lake 是 run.py 中的组件装配入口；Intake、SnapshotStore、QueryJobs、Results、Exporter、Reconciler 也可独立装配调用。组件不各自维护第二份数据库。

write(request_id,rows) 完整消费单遍源后发布，允许完整批次驻留内存。请求 ID 和观测 id 均为非空字符串，按原字符串识别。行严格包含 id/time/device/amount；后三项只能是非 bool 整数或 None，拒绝缺字段、额外字段、字符串数字和浮点。批内 id 唯一；跨批同 id 是不同来源，以 batch_id 和 ordinal 区分。空批合法。身份包含全部值、NULL、行数及顺序；同请求同内容返回首次回执，异内容或异序 ValueError。重放也完整消费并验证输入；源异常原样传播，无半批发布。

snapshot(snapshot_id=None) 返回当前或指定不可变快照副本，包含有序 batches 和 files。文件清单含 batch_id、day、digest、byte_count、row_count 和 ordinals。day 是 time//86400，负值向下取整，None 为独立时间分区。任意精度整数用规范 JSON 保存，分区日不存入 SQLite INTEGER 列。为支持 Python 3.11 超过默认十进制位数上限的整数，编解码器禁用该进程的整数文本位数限制。

create_query(request_id,snapshot_id,day_start,day_end,predicate=None) 返回 job_id。日端点为非 bool 整数，半开区间允许空、不许逆序。None 快照参数在创建时解析当前快照，随后保存确切 ID。查询请求同定义幂等、异定义拒绝。

predicate 为 None（TRUE）或列表/元组树：比较 [lt/le/eq/ne/ge/gt,time/device,非 bool 整数]，空值判断 [is_null,time/device]，二元 [and,left,right]、[or,left,right]，一元 [not,child]。拒绝未知算子、列、错误元数和循环。创建时复制成无环指令序列并持久化，不保留调用者引用。NULL 比较为 UNKNOWN；NOT UNKNOWN 仍 UNKNOWN，AND/OR 使用 SQL 三值逻辑，只有 TRUE 入选。只做逐行过滤：必要文件由固定日范围决定，范围内文件即使谓词无匹配行也必须读取。

run_query(job_id) 返回固定 result_id；完成任务重放直接返回原 ID。必要对象离线、丢失、摘要、结构、来源或日路由错误均抛 Unavailable，持久记录失败，不保存部分成果；修复原件后同任务可重试。成果封存与任务完成同事务；其他同步发布错误回滚，保留可重试状态。lake.queries.read_job(job_id) 可查看固定定义、pending/failed/complete、result_id 和错误。未知任务、快照或成果抛 NotFound；返回对象均为持久状态副本。

read_result(result_id) 返回 {result_id,kind,payload}。delivery 正文含 job_id、binding、rows、groups；rows 按快照批次接纳序再按原行号排序。groups 按 NULL 设备在前、整数设备升序；字段为 device/row_count/amount_count/sum_amount。全 NULL 金额的和为 None，非空零和为 0；全部计算为精确 Python 整数，空结果无组。

export_result(request_id,result_id,destination) 返回请求、成果、绝对目标路径、字节数及 SHA-256。目标父目录必须存在，目标为普通文件或尚不存在的文件；不接受符号链接和目录。内容是确定性 UTF-8 JSON 成果信封及一个换行。先完整写临时文件，备份旧目标，替换目标并提交回执；同步写入、替换附近或回执提交失败时恢复旧目标并撤销回执。回执是历史交付记录；重放不重写用户后来修改或删除的目标，新请求才是新交付。同步补偿不承诺跨文件系统和数据库的杀进程原子性；不提供并发操作协调。

reconcile(request_id,result_id) 只接受 delivery，返回固定核账成果 ID。它读取绑定快照全部对象，独立计算来源数、互斥原因、入选金额空值数、应入选原序明细及逐设备摘要，不调用交付累计器证明自身。分类优先级为 NULL 时间、范围外、FALSE、UNKNOWN、入选。核账正文含 binding/delivery_id/accounting/consistent/discrepancies。按来源成员、顺序、行值、组成员、笔数、有效金额数及精确和比较，总和相同也可能有差异。差异可保存；来源不可用是 Unavailable，不能保存虚假一致。已完成核账重放返回固定结果；改绑成果 ValueError。read_result 同时支持 delivery 和 reconciliation；核账成果不能再作为被核账交付或导出对象。

## 数据与失败边界

catalog.sqlite 保存批次、快照、回执、固定任务及成果；objects/ 保存规范不可变对象。对象含 batch_id 和 ordinal，摘要、长度和结构逐次验证。接纳在一笔 SQLite 事务内发布批次、快照、当前指针及回执；失败残留的孤立封存对象不影响可见性。没有对象回收、自动恢复、旧文件重写或未知版本重建；未知 schema 拒绝打开。单进程串行使用，没有并发协调或进程终止恢复承诺。

对象端口 lake.objects.set_available(digest,False/True) 持久改变可用性；read_trace 记录本句柄发出的读取请求；path_for(digest) 供本地原件恢复及确定性环境故障实验使用。范围外和 NULL 时间对象不阻断日查询，却属于首次完整核账的必要输入。Catalog 可选 hook(stage) 在真实写入边界调用，异常触发原事务回滚或导出补偿，不替代存储或业务计算。正常调用不需要 hook。测试只在自己的临时目录修改对象和输出，模拟离线、损坏及同步写入故障。

## 职责与回归

- records/acceptance/batch_identity/intake：严格接纳、单遍异常、有序身份和重放。
- day_partition/row_codec/object_store/file_builder：固定日路由、编码、封存及清单覆盖。
- catalog/snapshots/publication/receipts：目录生命周期及跨状态发布。
- predicates/day_pruning/row_reader/query_jobs：固定查询、必要对象、三值筛选及来源顺序。
- aggregation/result_builder/results/export：精确设备汇总、成果完整性及交付补偿。
- source_accounting/discrepancies/reconciliation_results/reconcile：完整来源分母、独立计算及固定差异。

原回归入口：test_acceptance 中 partition_and_replay、conflicting_request、source_exception；test_publication 中 failure_then_retry；test_delivery 中 exact_null_and_zero、export_failure；test_query 中 required_and_unrelated_object_fault；test_reconciliation 中 source_counts_and_reopen。

完整 discover 同时覆盖类型、负日、空批、提交边界、对象封存、结构错误、修复重试、参数冻结、三值真值表、大整数、NULL 组、返回副本、导出回执回滚、用户编辑、未知 schema 及等总和差异。预期来自手算及独立字面真值表，测试先断言再输出 POINTWISE_SCENARIO。所有运行只读取本项目材料，在指定业务目录或测试临时目录落盘。
