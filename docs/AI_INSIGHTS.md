# AI 洞察（服务器已启用，待填写密钥）

功能：影像分析完成后自动调用云端文本模型，展示结果摘要、定量解读、临床参考建议。
不生成诊断，不设置医生复核或签署流程。

## 配置

服务器文件：`/data/medimaging-analysis/backend/.env`，权限 600，已被 Git 忽略。
填写 `LLM_API_KEY=`。进程环境变量优先于 .env；不要在前端配置密钥。
地址默认 https://token.aigcit.com/v1，模型默认 deepseek-v4-flash。
`LLM_JSON_MODE=true` 需要网关支持 json_object；不会自动静默降级。
第三方网关的参数兼容性和真实模型质量尚需有密钥后验证。

## 审批与启用

1. 审阅 `backend/migrations/20261112_ai_insight_runs.sql`，用户批准后才执行。
2. 仅新增 ai_insight_runs 表及索引、关联 analysis_tasks 的外键，不修改已有表或历史报告。
3. 执行已批准迁移后设置 `LLM_WORKER_ENABLED=true`，再启动后端及发布前端构建。
4. 默认 `INITIALIZE_DATABASE_ON_STARTUP=false`，避免重启隐式建表和初始化账号。
5. 2026-11-12 用户已批准建表、运行时写入及启用服务；迁移已执行，Worker 已启用，前后端已更新。API Key 仍为空，没有真实云端模型调用，也未批量重新分析历史数据。

## 数据与流程

系统提示词：`backend/app/prompts/clinical_insight_v1.txt`。
输入仅传白名单定量数据；不发送姓名、Patient ID、DICOM UID、本地路径或整个 metadata。
不发送历史 heuristic 密度类型或球形度生成的边缘征象。只读取实际存在且有限的 raw feature 值。
强度单位通过原始 DICOM 与保存体数据校验，无法确认时保留未知。
当前 clinical_context 标记为未知，references 为空，因此不输出具体随访时间。
输入仅支持现有肺部 CT 候选病灶流程，不代表 MRI 等能力已经实现。

新分析会保存与当前影像/掩膜签名对应的来源记录，推理失败不再使用固定样本掩膜。
旧任务缺少来源记录时会显示资料不可用，需要重新运行真实影像分析。
新的推理任务使用独立目录，避免共享 sample_case 文件在并行任务间覆盖。

AI 洞察使用独立表，不更改 AnalysisTask 的完成状态，不占 GPU 门控，不覆盖历史报告。
Worker 在 FastAPI lifespan 中运行，MySQL 行锁及 active_task_id 唯一约束防重复。
每次调用有超时、租约及最大尝试次数；重启后可恢复超出租约的任务。
同一输入及配置复用上次结果，手动重新生成创建新版本。失败或重新生成时保留上次成功结果。
单次输入默认最多 50 个候选病灶、100000 字节，超出时明确提示，不静默截断。
重算组学后触发新输入，旧任务标记 SUPERSEDED；结果展示前也检查数据摘要。
不自动批量生成历史任务，防止启用密钥后意外外发和产生批量费用。

## API

- GET /api/v1/insights/{task_id}：只读获取状态、结构化结果及指标依据。
- POST /api/v1/insights/{task_id}/generate：生成或重试，需任务归属权限。
- 状态：NOT_ENABLED、NOT_GENERATED、WAITING_CONFIGURATION、QUEUED、RUNNING、
  SUCCEEDED、FAILED、BLOCKED、SUPERSEDED。
- status 表示执行状态；analysis_status 表示信息支持程度，不是诊断置信度。

## 验证边界

自动测试使用合成数据、Mock 云端和 Mock 数据库，不写生产数据库。
46 项后端测试通过；前端完成类型检查、隔离构建及合成数据浏览器状态验证。
结构、引用、测量值/单位及部分越界内容校验不能保证医学语义全部正确。
真实 MySQL JSON 读写、SKIP LOCKED 及活动任务唯一约束冒烟通过，测试写入全部回滚。线上鉴权、原有检查/结果接口、前端代理及新产物验证通过。
模型效果、真实网关兼容性、多 Worker 并发负载及完整模型调用链路仍需填入密钥后验证。
填写 .env 后需重启后端加载配置；进程环境变量优先于 .env。
\n部署记录与回退备份：\`/data/medimaging-analysis/.deployment/ai-insights-enable-20261112-190257\`。\n

## 定量解读 v2
新生成结果使用 clinical-insight-v2，仅包含摘要、定量解读、review_items 和局限。
不输出良恶性、诊断概率、治疗或随访周期。旧版结果保留，页面隐藏旧建议并提示重新生成。
输入使用匿名白名单指标和中性定义；不转发旧 clinical_summary 中的启发式定性标签。
新的特征提取记录实际 extractor 设置和软件版本；历史结果缺少该记录时明确标记，不追认默认值。
完整特征文件内容摘要参与缓存指纹。未记录医生复核、参考人群和IBSI验证时如实标记。
关键词拦截是保守补充，不能证明自然语言无临床越界；仍需代表性数据与医生验收。
本次不执行数据库迁移、历史回填或病例云端重试。
