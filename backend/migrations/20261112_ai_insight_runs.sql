-- APPROVAL REQUIRED. Add one table; no existing records changed.

CREATE TABLE ai_insight_runs (
	id BIGINT NOT NULL AUTO_INCREMENT,
	task_id BIGINT NOT NULL,
	active_task_id BIGINT,
	input_hash VARCHAR(64) NOT NULL,
	prompt_hash VARCHAR(64) NOT NULL,
	prompt_version VARCHAR(64) NOT NULL,
	model VARCHAR(128) NOT NULL,
	status VARCHAR(32) NOT NULL,
	input_snapshot JSON NOT NULL,
	result_json JSON,
	usage_json JSON,
	error_message TEXT,
	attempts INTEGER NOT NULL,
	available_at DATETIME NOT NULL,
	lease_token VARCHAR(64),
	lease_until DATETIME,
	created_at DATETIME NOT NULL,
	finished_at DATETIME,
	PRIMARY KEY (id),
	FOREIGN KEY(task_id) REFERENCES analysis_tasks (id) ON DELETE CASCADE,
	UNIQUE (active_task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
CREATE INDEX ix_ai_insight_runs_status ON ai_insight_runs (status);
CREATE INDEX ix_ai_insight_runs_task_id ON ai_insight_runs (task_id);
