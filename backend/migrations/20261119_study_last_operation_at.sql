-- Apply only after approval, before loading the backend that maps this column.
ALTER TABLE studies
    ADD COLUMN last_operation_at DATETIME(6) NULL;
