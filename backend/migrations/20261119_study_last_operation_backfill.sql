-- Run separately after approval and after adding studies.last_operation_at.
-- Use the latest submission by the study owner and preserve newer activity.
UPDATE studies AS study
JOIN (
    SELECT study_id, doctor_id, MAX(created_at) AS last_submitted_at
    FROM analysis_tasks
    GROUP BY study_id, doctor_id
) AS submissions
    ON submissions.study_id = study.id AND submissions.doctor_id = study.doctor_id
SET study.last_operation_at = submissions.last_submitted_at
WHERE study.last_operation_at IS NULL
   OR study.last_operation_at < submissions.last_submitted_at;
