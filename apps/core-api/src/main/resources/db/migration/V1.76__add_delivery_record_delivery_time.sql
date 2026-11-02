-- ============================================================
-- V1.76__add_delivery_record_delivery_time.sql
-- 给 delivery_record 补齐 delivery_time 列
--
-- 背景：
-- ws_delivery_handler._insert_delivery_record 会写入 delivery_time，
-- 但历史数据库没有该列，导致自动发货在写入 delivery_record 时抛
-- Unknown column 'delivery_time' in 'field list'，卡密已选中但发货记录无法落库。
--
-- 纯加列，非破坏性，幂等可重入。
-- ============================================================

SET @col_exists := (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'delivery_record' AND COLUMN_NAME = 'delivery_time');
SET @sql_add_col := IF(@col_exists = 0,
    'ALTER TABLE delivery_record ADD COLUMN delivery_time DATETIME DEFAULT NULL COMMENT ''发货时间''',
    'SELECT 1');
PREPARE stmt_add_col FROM @sql_add_col;
EXECUTE stmt_add_col;
DEALLOCATE PREPARE stmt_add_col;
