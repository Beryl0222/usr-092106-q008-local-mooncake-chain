# 领域说明：乡味月饼产销协作账

屯昌多家小工坊在中秋季共同承接叉烧五仁月饼订单。本资料用只追加事件
（event-sourced）记录协作全链，并围绕六件难事给出统一语义：

1. 一张消费者退货单能反查成品、班次、配方与原料供应户；
2. 一批异常原料能找全受影响订单，又不扩大冻结范围；
3. 扫码公开产地与制作信息，但不带出村民收入；
4. 地方配方可以不同，标签必须反映实际过敏原；
5. 预售取消、临期转渠道、节前加单在产能内重排，缺料与交付风险提前可见；
6. 线下称重与工时补录全局只能付款一次。

## 事件账本

- 事件一经接收，`event_id`、`occurred_at`、`version` 不可原地改写；
  业务更正只能追加后继事件（如重新固定标签、隔离批次、召回）。
- 同一 `aggregate_id` 下 `version` 从 1 起严格递增（`src/ledger.py` 强制）。
- 线下称重单 `weigh_no`、工时补录单 `supplement_no`、付款单号进入
  `idempotency_key`，同号第二次入账直接拒绝。
- `contracts/domain.schema.json` 与 `src/validator.py` 是同一份约定的两种表达，
  由 `tests/test_contract.py` 交叉核对；枚举不一致会使测试失败。

## 标识（跨工坊衔接用）

| 标识 | 含义 |
| --- | --- |
| `supplier_id`（SUP-*） | 供应户。姓名、银行尾号、单价只在内部侧出现 |
| `workshop_id`（WS-*） | 工坊，绑定资质编号与日产能 |
| 原料批次 `lot_id`（LOT-*） | 供应户 + 天进场的一批原料，带保质期天数 |
| `recipe_id` + `recipe_version` | 配方版本。地方配方各自注册、各自批准 |
| `shift_id`（SHIFT-*） | 生产班次，绑定工坊、日期、当班师傅与计划量 |
| `batch_id`（BATCH-*） | 手工成品批次，钉住实际投料批次 |
| `order_id`（O-*） | 预留订单/加单；`plan_id` 串联一次重排 |
| `shipment_id`（SHIP-*） | 渠道发货，退货挂在同一发货上 |
| `recall_id` | 召回，冻结范围由其 `scope_lot_ids` 机械推导 |

## 双向追溯与精确冻结

链边只有一条事实：`BATCH_PRODUCED.payload.ingredient_lots`（成品实际投了哪些批）。

- 反向 `return_provenance(SHIP-3001)`：退货 → 成品批次 → 班次/师傅/工坊 →
  配方版本 → 每个投料批次（数量、检验状态、供应户、进场日期）。
- 正向 `affected_orders([lot_id])`：异常原料 → 使用过它的成品 → 占用中的
  预留订单与全部发货（含已退货，便于区分拦截与回收）。
- 冻结 `freeze_recall_batches(recall_id)`：只为谱系中真实受影响且尚未冻结的
  批次追加 `BATCH_BLOCKED`；重复执行不再产生事件。同品类不同批次
  （如 LOT-CS-0916 vs LOT-CS-0918）互不牵连。

## 过敏原与标签

- 受控词表：花生、坚果、蛋、奶、小麦麸质、大豆、芝麻。
- 原料天然引入的过敏原在 `src/quality.py` 的 `INGREDIENT_ALLERGENS` 中维护
  （叉烧腌制含酱油 ⇒ 大豆 + 小麦麸质）。
- 放行门槛 `can_release`：标签必须覆盖按**实际投料**推导出的全部过敏原，
  且标签快照的配方版本等于投产版本；漏标即不得放行。
- 枫木坊“少芝麻版”是获准的不同配方，其标签合法地不含芝麻——
  差异来自配方，而不是标签自由发挥。

## 隐私公开投影

`src/privacy.py` 用白名单构造扫码视图：产品、工坊与资质、班次日期、
配方版本、过敏原、原料品类与**村片区**、检验结果。

显式不公开：供应户姓名与 ID、单价/金额/时薪、银行尾号。投影末尾有一道
敏感字段名扫描（price/rate/amount/account/pay/supplier），混入即抛错；
冻结或未放行批次不返回内容。

## 产能内重排

`src/planning.py` 的 `reassign` 一次处理三类触发，产出草案事件，不直接改状态：

- **预售取消**：`ORDER_CANCELLED` 释放的库存优先用于本次加单（取消批次优先）；
- **临期转渠道**：`rechannel_near_expiry` 按到期日给建议，转渠道量从可售池扣减，
  超量拒绝；
- **节前加单**：先找能整批满足的成品，排不下再按临期顺序拆配；
  交付日前过期的批次不参与。排不进的量进入 `unmet_orders` 并追加
  `RISK_FLAG_RAISED(缺料)`。

硬约束：只能用已放行、未冻结、保质期内的库存；排产受工坊日产能
（班次 planned_kg 已占部分）与资质有效期限制。`delivery_risks` 另外预警
“交付日晚于保质期”和“资质 7 天内到期”。

## 结算：只能付一次

三类可付款来源：

| 来源事件 | 结算引用号 | 金额 |
| --- | --- | --- |
| `WEIGH_IN_RECORDED` | `weigh_no` | 数量 × 单价 |
| `WORK_ATTENDANCE_RECORDED` | `att:{worker}:{shift}` | 工时 × 时薪 |
| `WORK_HOURS_SUPPLEMENTED` | `supplement_no` | 工时 × 时薪 |

`build_payment` 按来源**自动汇总金额**（调用方不能手填）、校验收款人一致；
`SETTLEMENT_PAID.payload.source_refs` 钉住被结清的来源。领域层拒绝来源二次
支付，账本层用 `idempotency_key` 兜底，连同号补录事件本身都无法再次入账。

## 代码地图

```
contracts/domain.schema.json   事件契约（26 类事件、10 类聚合，重排单挂 production_plan）
src/validator.py               纯标准库的信封/载荷校验
src/ledger.py                  只追加账本：版本连续、ID 与幂等键唯一
src/readmodel.py               由事件流重建的世界快照（库存、可售量等派生量）
src/quality.py                 投产与放行门槛、过敏原推导
src/traceability.py            双向追溯、影响面、精确冻结范围
src/privacy.py                 扫码公开白名单投影
src/planning.py                取消/临期/加单的产能内重排与风险旗标
src/settlement.py              称重/工时/补录的一次付款
src/commands.py                信封构造、草案提交、冻结与结算落账
tests/fixtures.py              2026 中秋季两工坊完整场景（45 条事件）
data/sample_events.jsonl       该场景的全部事件，可直接联调
```
