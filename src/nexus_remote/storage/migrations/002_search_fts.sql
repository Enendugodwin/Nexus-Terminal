-- 002_search_fts.sql - full-text search indexes (only applied when FTS5 exists).

CREATE VIRTUAL TABLE IF NOT EXISTS command_history_fts USING fts5(
    command,
    notes,
    content='command_history',
    content_rowid='id',
    tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS command_history_ai AFTER INSERT ON command_history BEGIN
    INSERT INTO command_history_fts(rowid, command, notes)
    VALUES (new.id, new.command, new.notes);
END;

CREATE TRIGGER IF NOT EXISTS command_history_ad AFTER DELETE ON command_history BEGIN
    INSERT INTO command_history_fts(command_history_fts, rowid, command, notes)
    VALUES ('delete', old.id, old.command, old.notes);
END;

CREATE TRIGGER IF NOT EXISTS command_history_au AFTER UPDATE ON command_history BEGIN
    INSERT INTO command_history_fts(command_history_fts, rowid, command, notes)
    VALUES ('delete', old.id, old.command, old.notes);
    INSERT INTO command_history_fts(rowid, command, notes)
    VALUES (new.id, new.command, new.notes);
END;

CREATE VIRTUAL TABLE IF NOT EXISTS command_snippets_fts USING fts5(
    name,
    command,
    description,
    content='command_snippets',
    content_rowid='rowid',
    tokenize='unicode61'
);

CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
    title,
    body,
    content='documents',
    content_rowid='rowid',
    tokenize='unicode61'
);
