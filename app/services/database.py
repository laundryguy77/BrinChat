import sqlite3
import logging
import uuid
from pathlib import Path
from contextlib import contextmanager
from typing import Optional, Generator
import threading

logger = logging.getLogger(__name__)

# Thread-local storage for connections
_local = threading.local()


class DatabaseError(Exception):
    """Generic database error with sanitized message.

    Used to wrap raw SQLite errors to prevent information disclosure.
    The original error is logged internally but not exposed to callers.
    """
    pass


class DatabaseService:
    """SQLite database service with connection management and migrations"""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self._ensure_db_directory()
        self._run_migrations()

    def _ensure_db_directory(self):
        """Ensure the database directory exists"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def _get_connection(self) -> sqlite3.Connection:
        """Get a thread-local database connection"""
        if not hasattr(_local, 'connection') or _local.connection is None:
            _local.connection = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False
            )
            _local.connection.row_factory = sqlite3.Row
            # Enable foreign keys
            _local.connection.execute("PRAGMA foreign_keys = ON")
        return _local.connection

    def _generate_error_id(self) -> str:
        """Generate a unique error ID for tracking."""
        return str(uuid.uuid4())[:8]

    @contextmanager
    def get_cursor(self) -> Generator[sqlite3.Cursor, None, None]:
        """Context manager for database cursor with automatic commit/rollback"""
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            yield cursor
            conn.commit()
        except sqlite3.Error as e:
            conn.rollback()
            error_id = self._generate_error_id()
            logger.error(f"Database error [{error_id}]: {e}")
            raise DatabaseError(f"Database operation failed (ref: {error_id})") from None
        except Exception as e:
            conn.rollback()
            error_id = self._generate_error_id()
            logger.error(f"Unexpected error [{error_id}]: {e}")
            raise
        finally:
            cursor.close()

    @contextmanager
    def transaction(self) -> Generator["TransactionContext", None, None]:
        """Context manager for atomic multi-statement transactions.

        Usage:
            with db.transaction() as tx:
                tx.execute("INSERT INTO users ...", params1)
                tx.execute("INSERT INTO settings ...", params2)
                # Both succeed or both fail

        If any exception occurs within the context, all changes are rolled back.
        """
        conn = self._get_connection()
        ctx = TransactionContext(conn)
        try:
            yield ctx
            conn.commit()
        except sqlite3.Error as e:
            conn.rollback()
            error_id = self._generate_error_id()
            logger.error(f"Transaction failed [{error_id}], rolled back: {e}")
            raise DatabaseError(f"Transaction failed (ref: {error_id})") from None
        except Exception as e:
            conn.rollback()
            error_id = self._generate_error_id()
            logger.error(f"Transaction failed [{error_id}], rolled back: {e}")
            raise

    def execute(self, query: str, params: tuple = ()) -> sqlite3.Cursor:
        """Execute a query and return the cursor"""
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(query, params)
            conn.commit()
            return cursor
        except sqlite3.Error as e:
            conn.rollback()
            error_id = self._generate_error_id()
            logger.error(f"Execute failed [{error_id}]: {e}")
            raise DatabaseError(f"Database operation failed (ref: {error_id})") from None

    def executemany(self, query: str, params_list: list) -> sqlite3.Cursor:
        """Execute a query with multiple parameter sets"""
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            cursor.executemany(query, params_list)
            conn.commit()
            return cursor
        except sqlite3.Error as e:
            conn.rollback()
            error_id = self._generate_error_id()
            logger.error(f"Executemany failed [{error_id}]: {e}")
            raise DatabaseError(f"Database operation failed (ref: {error_id})") from None

    def fetchone(self, query: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        """Execute query and fetch one result"""
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(query, params)
            return cursor.fetchone()
        except sqlite3.Error as e:
            error_id = self._generate_error_id()
            logger.error(f"Fetchone failed [{error_id}]: {e}")
            raise DatabaseError(f"Database operation failed (ref: {error_id})") from None

    def fetchall(self, query: str, params: tuple = ()) -> list:
        """Execute query and fetch all results"""
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(query, params)
            return cursor.fetchall()
        except sqlite3.Error as e:
            error_id = self._generate_error_id()
            logger.error(f"Fetchall failed [{error_id}]: {e}")
            raise DatabaseError(f"Database operation failed (ref: {error_id})") from None

    def _run_migrations(self):
        """Run database migrations"""
        logger.info(f"Running database migrations on {self.db_path}")

        # Create migrations table if it doesn't exist
        self.execute("""
            CREATE TABLE IF NOT EXISTS migrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Define migrations
        migrations = [
            ("001_create_users", self._migration_001_create_users),
            ("002_create_user_settings", self._migration_002_create_user_settings),
            ("003_create_conversations", self._migration_003_create_conversations),
            ("004_create_documents", self._migration_004_create_documents),
            ("005_create_chunks", self._migration_005_create_chunks),
            ("006_remove_tts_columns", self._migration_006_remove_tts_columns),
            ("007_create_memories", self._migration_007_create_memories),
            ("008_create_mcp_servers", self._migration_008_create_mcp_servers),
            ("009_create_user_profiles", self._migration_009_create_user_profiles),
            ("010_add_full_unlock_columns", self._migration_010_add_full_unlock_columns),
            ("011_add_voice_settings", self._migration_011_add_voice_settings),
            ("012_admin_features", self._migration_012_admin_features),
            ("013_add_auto_send_voice", self._migration_013_add_auto_send_voice),
            ("014_brinboard_projects", self._migration_014_brinboard_projects),
            ("015_brinboard_agents", self._migration_015_brinboard_agents),
            ("016_brinboard_tasks", self._migration_016_brinboard_tasks),
            ("017_brinboard_hooks", self._migration_017_brinboard_hooks),
            ("018_brinboard_sessions", self._migration_018_brinboard_sessions),
            ("019_brinboard_attachments_comments", self._migration_019_brinboard_attachments_comments),
            ("020_brinboard_tags", self._migration_020_brinboard_tags),
        ]

        # Run pending migrations
        for name, migration_func in migrations:
            if not self._migration_applied(name):
                logger.info(f"Applying migration: {name}")
                try:
                    migration_func()
                    self._mark_migration_applied(name)
                    logger.info(f"Migration {name} applied successfully")
                except Exception as e:
                    logger.error(f"Migration {name} failed: {e}")
                    raise

    def _migration_applied(self, name: str) -> bool:
        """Check if a migration has been applied"""
        result = self.fetchone(
            "SELECT 1 FROM migrations WHERE name = ?",
            (name,)
        )
        return result is not None

    def _mark_migration_applied(self, name: str):
        """Mark a migration as applied"""
        self.execute(
            "INSERT INTO migrations (name) VALUES (?)",
            (name,)
        )

    def _migration_001_create_users(self):
        """Create users table"""
        self.execute("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                email TEXT UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.execute("CREATE INDEX idx_users_username ON users(username)")
        self.execute("CREATE INDEX idx_users_email ON users(email)")

    def _migration_002_create_user_settings(self):
        """Create user_settings table.

        NOTE: This migration creates TTS columns that are no longer used.
        Migration 006 (_migration_006_remove_tts_columns) removes them.
        """
        self.execute("""
            CREATE TABLE user_settings (
                user_id INTEGER PRIMARY KEY,
                model TEXT,
                temperature REAL,
                top_p REAL,
                top_k INTEGER,
                num_ctx INTEGER,
                repeat_penalty REAL,
                persona TEXT,
                tts_enabled INTEGER DEFAULT 0,
                tts_speaker INTEGER DEFAULT 0,
                tts_temperature REAL DEFAULT 0.9,
                tts_topk INTEGER DEFAULT 50,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)

    def _migration_003_create_conversations(self):
        """Create conversations table"""
        self.execute("""
            CREATE TABLE conversations (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                title TEXT,
                model TEXT,
                created_at TEXT,
                updated_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        self.execute("CREATE INDEX idx_conversations_user_id ON conversations(user_id)")
        self.execute("CREATE INDEX idx_conversations_updated_at ON conversations(updated_at)")

    def _migration_004_create_documents(self):
        """Create documents table for knowledge base"""
        self.execute("""
            CREATE TABLE documents (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                filename TEXT NOT NULL,
                file_type TEXT,
                file_hash TEXT,
                chunk_count INTEGER DEFAULT 0,
                embedding_model TEXT,
                created_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        self.execute("CREATE INDEX idx_documents_user_id ON documents(user_id)")
        self.execute("CREATE INDEX idx_documents_file_hash ON documents(file_hash)")

    def _migration_005_create_chunks(self):
        """Create chunks table for document embeddings"""
        self.execute("""
            CREATE TABLE chunks (
                id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                content TEXT NOT NULL,
                embedding TEXT,
                FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
            )
        """)
        self.execute("CREATE INDEX idx_chunks_document_id ON chunks(document_id)")

    def _migration_006_remove_tts_columns(self):
        """Remove deprecated TTS columns from user_settings"""
        # SQLite doesn't support DROP COLUMN directly, recreate the table
        self.execute("""
            CREATE TABLE user_settings_new (
                user_id INTEGER PRIMARY KEY,
                model TEXT,
                temperature REAL,
                top_p REAL,
                top_k INTEGER,
                num_ctx INTEGER,
                repeat_penalty REAL,
                persona TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)

        # Copy data (excluding TTS columns)
        self.execute("""
            INSERT INTO user_settings_new (user_id, model, temperature, top_p, top_k, num_ctx, repeat_penalty, persona)
            SELECT user_id, model, temperature, top_p, top_k, num_ctx, repeat_penalty, persona
            FROM user_settings
        """)

        # Drop old table and rename
        self.execute("DROP TABLE user_settings")
        self.execute("ALTER TABLE user_settings_new RENAME TO user_settings")

    def _migration_007_create_memories(self):
        """Create memories table for user memory storage."""
        self.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                category TEXT DEFAULT 'general',
                importance INTEGER DEFAULT 5,
                embedding TEXT,
                source TEXT DEFAULT 'inferred',
                created_at TEXT,
                last_accessed TEXT,
                access_count INTEGER DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        self.execute("CREATE INDEX IF NOT EXISTS idx_memories_user_id ON memories(user_id)")
        self.execute("CREATE INDEX IF NOT EXISTS idx_memories_category ON memories(category)")

    def _migration_008_create_mcp_servers(self):
        """Create mcp_servers table for MCP server configurations."""
        self.execute("""
            CREATE TABLE IF NOT EXISTS mcp_servers (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                transport TEXT NOT NULL,
                command TEXT,
                args TEXT,
                url TEXT,
                env TEXT,
                enabled INTEGER DEFAULT 1,
                created_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        self.execute("CREATE INDEX IF NOT EXISTS idx_mcp_servers_user_id ON mcp_servers(user_id)")

    def _migration_009_create_user_profiles(self):
        """Create user_profiles table for user profile system."""
        self.execute("""
            CREATE TABLE IF NOT EXISTS user_profiles (
                user_id INTEGER PRIMARY KEY,
                profile_data TEXT NOT NULL DEFAULT '{}',
                adult_mode_enabled INTEGER DEFAULT 0,
                adult_mode_unlocked_at TEXT,
                created_at TEXT,
                updated_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        self.execute("CREATE INDEX IF NOT EXISTS idx_user_profiles_user_id ON user_profiles(user_id)")

    def _migration_010_add_full_unlock_columns(self):
        """Add full_unlock_enabled and full_unlock_at columns to user_profiles.

        This enforces the two-tier unlock system:
        - Tier 1 (adult_mode_enabled): Unlocked via passcode in Settings
        - Tier 2 (full_unlock_enabled): Unlocked via /full_unlock command in chat

        Migration also auto-enables full_unlock for existing users who already have
        all sensitive sections enabled AND adult_mode_enabled=True.
        """
        # Add the new columns
        self.execute("""
            ALTER TABLE user_profiles
            ADD COLUMN full_unlock_enabled INTEGER DEFAULT 0
        """)
        self.execute("""
            ALTER TABLE user_profiles
            ADD COLUMN full_unlock_at TEXT
        """)

        # Auto-migrate existing users who have adult_mode + all sensitive sections enabled
        rows = self.fetchall("""
            SELECT user_id, profile_data, adult_mode_enabled
            FROM user_profiles
            WHERE adult_mode_enabled = 1
        """)

        import json
        from datetime import datetime

        for row in rows:
            try:
                profile_data = json.loads(row["profile_data"])
                sensitive_sections = ["sexual_romantic", "dark_content", "private_self", "substances_health"]
                all_enabled = all(
                    profile_data.get(section, {}).get("enabled", False)
                    for section in sensitive_sections
                )

                if all_enabled:
                    now = datetime.utcnow().isoformat() + "Z"
                    self.execute(
                        """UPDATE user_profiles
                           SET full_unlock_enabled = 1, full_unlock_at = ?
                           WHERE user_id = ?""",
                        (now, row["user_id"])
                    )
                    logger.info(f"Auto-migrated full_unlock for user {row['user_id']}")
            except Exception as e:
                logger.warning(f"Failed to check user {row['user_id']} for full_unlock migration: {e}")

    def _migration_011_add_voice_settings(self):
        """Add voice settings columns to user_settings table.

        Adds columns for voice feature configuration:
        - voice_mode: disabled, transcribe_only, tts_only, conversation
        - tts_voice: voice preset name
        - tts_speed: playback speed multiplier
        - auto_play: whether to auto-play TTS responses
        - stt_language: language code for speech-to-text
        """
        self.execute("""
            ALTER TABLE user_settings
            ADD COLUMN voice_mode TEXT DEFAULT 'disabled'
        """)
        self.execute("""
            ALTER TABLE user_settings
            ADD COLUMN tts_voice TEXT DEFAULT 'default'
        """)
        self.execute("""
            ALTER TABLE user_settings
            ADD COLUMN tts_speed REAL DEFAULT 1.0
        """)
        self.execute("""
            ALTER TABLE user_settings
            ADD COLUMN auto_play INTEGER DEFAULT 0
        """)
        self.execute("""
            ALTER TABLE user_settings
            ADD COLUMN stt_language TEXT DEFAULT 'en'
        """)

    def _migration_012_admin_features(self):
        """Add admin features: user management, feature flags, themes, and audit logging.

        This migration adds:
        - is_admin, is_active, mode_restriction columns to users table
        - feature_flags table for global feature definitions
        - user_feature_overrides table for per-user feature toggles
        - admin_audit_log table for admin action tracking
        - themes table for custom UI themes
        """
        # Add admin columns to users table
        self.execute("""
            ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0
        """)
        self.execute("""
            ALTER TABLE users ADD COLUMN is_active INTEGER DEFAULT 1
        """)
        self.execute("""
            ALTER TABLE users ADD COLUMN mode_restriction TEXT DEFAULT NULL
        """)

        # Create feature_flags table
        self.execute("""
            CREATE TABLE IF NOT EXISTS feature_flags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                feature_key TEXT UNIQUE NOT NULL,
                display_name TEXT NOT NULL,
                description TEXT,
                default_enabled INTEGER DEFAULT 1,
                category TEXT DEFAULT 'general',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Create user_feature_overrides table
        self.execute("""
            CREATE TABLE IF NOT EXISTS user_feature_overrides (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                feature_key TEXT NOT NULL,
                enabled INTEGER NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                created_by INTEGER,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                UNIQUE(user_id, feature_key)
            )
        """)

        # Create admin_audit_log table
        self.execute("""
            CREATE TABLE IF NOT EXISTS admin_audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                target_type TEXT NOT NULL,
                target_id TEXT,
                details TEXT,
                ip_address TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (admin_id) REFERENCES users(id)
            )
        """)

        # Create themes table
        self.execute("""
            CREATE TABLE IF NOT EXISTS themes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                display_name TEXT NOT NULL,
                description TEXT,
                css_variables TEXT NOT NULL,
                is_system INTEGER DEFAULT 0,
                is_enabled INTEGER DEFAULT 1,
                created_by INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (created_by) REFERENCES users(id)
            )
        """)

        # Insert default feature flags
        default_features = [
            ('tool_use', 'Tool Use', 'Allow model to use tools', 1, 'tools'),
            ('web_search', 'Web Search', 'Allow web search tool', 1, 'tools'),
            ('browse_website', 'URL Browsing', 'Allow URL fetching tool', 1, 'tools'),
            ('image_generation', 'Image Generation', 'Allow image generation', 1, 'tools'),
            ('video_generation', 'Video Generation', 'Allow video generation', 1, 'tools'),
            ('memory_system', 'Memory System', 'Enable persistent memory', 1, 'memory'),
            ('knowledge_base', 'Knowledge Base', 'Enable knowledge base', 1, 'memory'),
            ('tts', 'Text-to-Speech', 'Enable TTS for responses', 1, 'voice'),
            ('stt', 'Speech-to-Text', 'Enable voice input', 1, 'voice'),
            ('thinking_mode', 'Thinking Mode', 'Show model reasoning', 1, 'display'),
            ('mcp_tools', 'MCP Tools', 'Enable MCP server tools', 1, 'tools'),
        ]
        for feature in default_features:
            self.execute("""
                INSERT OR IGNORE INTO feature_flags
                (feature_key, display_name, description, default_enabled, category)
                VALUES (?, ?, ?, ?, ?)
            """, feature)

        # Insert default themes
        import json
        default_themes = [
            ('dark', 'Default Dark', 'Classic dark theme',
             json.dumps({"--bg-primary": "#0f172a", "--bg-surface": "#1e293b",
                        "--text-primary": "#ffffff", "--text-secondary": "#94a3b8",
                        "--accent": "#144bb8", "--border": "#334155"}), 1),
            ('midnight', 'Midnight Blue', 'Deep blue tones',
             json.dumps({"--bg-primary": "#0d1117", "--bg-surface": "#161b22",
                        "--text-primary": "#c9d1d9", "--text-secondary": "#8b949e",
                        "--accent": "#58a6ff", "--border": "#30363d"}), 1),
            ('forest', 'Forest Green', 'Natural green palette',
             json.dumps({"--bg-primary": "#0f1a0f", "--bg-surface": "#1a2f1a",
                        "--text-primary": "#e8f5e9", "--text-secondary": "#a5d6a7",
                        "--accent": "#4caf50", "--border": "#2e5030"}), 1),
        ]
        for theme in default_themes:
            self.execute("""
                INSERT OR IGNORE INTO themes
                (name, display_name, description, css_variables, is_system)
                VALUES (?, ?, ?, ?, ?)
            """, theme)

        # Create indexes for performance
        self.execute("CREATE INDEX IF NOT EXISTS idx_feature_overrides_user ON user_feature_overrides(user_id)")
        self.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_admin ON admin_audit_log(admin_id)")
        self.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_created ON admin_audit_log(created_at)")

    def _migration_013_add_auto_send_voice(self):
        """Add auto_send column to user_settings for voice assistant mode.

        When enabled (default True), voice transcriptions are automatically
        sent to the chat instead of being inserted into the input field.
        """
        self.execute("""
            ALTER TABLE user_settings
            ADD COLUMN auto_send INTEGER DEFAULT 1
        """)

    def _migration_014_brinboard_projects(self):
        """Create bb_projects table for BrinBoard"""
        self.execute("""
            CREATE TABLE IF NOT EXISTS bb_projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                prompt TEXT DEFAULT '',
                owner_id INTEGER NOT NULL,
                settings TEXT DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                archived INTEGER DEFAULT 0,
                FOREIGN KEY (owner_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        self.execute("CREATE INDEX IF NOT EXISTS idx_bb_projects_owner ON bb_projects(owner_id)")
        self.execute("CREATE INDEX IF NOT EXISTS idx_bb_projects_archived ON bb_projects(archived)")

    def _migration_015_brinboard_agents(self):
        """Create bb_agents table for BrinBoard (before bb_tasks to handle circular FK)"""
        self.execute("""
            CREATE TABLE IF NOT EXISTS bb_agents (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                status TEXT DEFAULT 'offline' CHECK(status IN ('idle','active','input_needed','processing','offline')),
                current_task_id TEXT,
                health INTEGER DEFAULT 100,
                last_seen TEXT,
                metadata TEXT DEFAULT '{}',
                created_at TEXT NOT NULL
            )
        """)

    def _migration_016_brinboard_tasks(self):
        """Create bb_tasks table for BrinBoard"""
        self.execute("""
            CREATE TABLE IF NOT EXISTS bb_tasks (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT,
                prompt TEXT,
                project_id TEXT,
                parent_id TEXT,
                assignee_id TEXT,
                status TEXT DEFAULT 'idle' CHECK(status IN ('idle','active','user_input_needed','finished','archived')),
                priority TEXT DEFAULT 'medium' CHECK(priority IN ('critical','high','medium','low')),
                position INTEGER DEFAULT 0,
                settings TEXT DEFAULT '{}',
                due_date TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (project_id) REFERENCES bb_projects(id) ON DELETE SET NULL,
                FOREIGN KEY (parent_id) REFERENCES bb_tasks(id) ON DELETE CASCADE,
                FOREIGN KEY (assignee_id) REFERENCES bb_agents(id) ON DELETE SET NULL
            )
        """)
        self.execute("CREATE INDEX IF NOT EXISTS idx_bb_tasks_project ON bb_tasks(project_id)")
        self.execute("CREATE INDEX IF NOT EXISTS idx_bb_tasks_status ON bb_tasks(status)")
        self.execute("CREATE INDEX IF NOT EXISTS idx_bb_tasks_assignee ON bb_tasks(assignee_id)")
        self.execute("CREATE INDEX IF NOT EXISTS idx_bb_tasks_parent ON bb_tasks(parent_id)")

        # Now add FK constraint to bb_agents.current_task_id (deferred FK)
        # SQLite doesn't allow adding FKs after table creation, so this is just a logical reference

    def _migration_017_brinboard_hooks(self):
        """Create bb_hooks table for BrinBoard"""
        self.execute("""
            CREATE TABLE IF NOT EXISTS bb_hooks (
                id TEXT PRIMARY KEY,
                project_id TEXT,
                task_id TEXT,
                name TEXT NOT NULL,
                description TEXT,
                event TEXT NOT NULL,
                action_type TEXT NOT NULL CHECK(action_type IN ('run_prompt','run_script','log_metadata','webhook','block')),
                action_data TEXT DEFAULT '{}',
                enabled INTEGER DEFAULT 1,
                position INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (project_id) REFERENCES bb_projects(id) ON DELETE CASCADE,
                FOREIGN KEY (task_id) REFERENCES bb_tasks(id) ON DELETE CASCADE,
                CHECK (project_id IS NOT NULL OR task_id IS NOT NULL)
            )
        """)
        self.execute("CREATE INDEX IF NOT EXISTS idx_bb_hooks_project ON bb_hooks(project_id)")
        self.execute("CREATE INDEX IF NOT EXISTS idx_bb_hooks_task ON bb_hooks(task_id)")

    def _migration_018_brinboard_sessions(self):
        """Create bb_agent_sessions table for BrinBoard"""
        self.execute("""
            CREATE TABLE IF NOT EXISTS bb_agent_sessions (
                id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                task_id TEXT,
                project_id TEXT,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                status TEXT DEFAULT 'running' CHECK(status IN ('running','completed','failed','cancelled')),
                context_size_start INTEGER DEFAULT 0,
                context_size_end INTEGER,
                compact_count INTEGER DEFAULT 0,
                FOREIGN KEY (agent_id) REFERENCES bb_agents(id) ON DELETE CASCADE,
                FOREIGN KEY (task_id) REFERENCES bb_tasks(id) ON DELETE SET NULL,
                FOREIGN KEY (project_id) REFERENCES bb_projects(id) ON DELETE SET NULL
            )
        """)
        self.execute("CREATE INDEX IF NOT EXISTS idx_bb_sessions_agent ON bb_agent_sessions(agent_id)")

    def _migration_019_brinboard_attachments_comments(self):
        """Create bb_attachments and bb_comments tables for BrinBoard"""
        self.execute("""
            CREATE TABLE IF NOT EXISTS bb_attachments (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                filepath TEXT NOT NULL,
                mime_type TEXT,
                size_bytes INTEGER,
                uploaded_by INTEGER,
                created_at TEXT NOT NULL,
                FOREIGN KEY (task_id) REFERENCES bb_tasks(id) ON DELETE CASCADE,
                FOREIGN KEY (uploaded_by) REFERENCES users(id)
            )
        """)

        self.execute("""
            CREATE TABLE IF NOT EXISTS bb_comments (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                user_id INTEGER,
                agent_id TEXT,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (task_id) REFERENCES bb_tasks(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (agent_id) REFERENCES bb_agents(id)
            )
        """)
        self.execute("CREATE INDEX IF NOT EXISTS idx_bb_comments_task ON bb_comments(task_id)")

    def _migration_020_brinboard_tags(self):
        """Create bb_tags and bb_task_tags tables for BrinBoard"""
        self.execute("""
            CREATE TABLE IF NOT EXISTS bb_tags (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                color TEXT DEFAULT '#3b82f6'
            )
        """)

        self.execute("""
            CREATE TABLE IF NOT EXISTS bb_task_tags (
                task_id TEXT NOT NULL,
                tag_id TEXT NOT NULL,
                PRIMARY KEY (task_id, tag_id),
                FOREIGN KEY (task_id) REFERENCES bb_tasks(id) ON DELETE CASCADE,
                FOREIGN KEY (tag_id) REFERENCES bb_tags(id) ON DELETE CASCADE
            )
        """)

    def close(self):
        """Close the database connection"""
        if hasattr(_local, 'connection') and _local.connection is not None:
            _local.connection.close()
            _local.connection = None


class TransactionContext:
    """Helper class for executing multiple statements in a transaction."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def execute(self, query: str, params: tuple = ()) -> sqlite3.Cursor:
        """Execute a query within this transaction (no auto-commit)."""
        cursor = self._conn.cursor()
        cursor.execute(query, params)
        return cursor

    def executemany(self, query: str, params_list: list) -> sqlite3.Cursor:
        """Execute a query with multiple parameter sets (no auto-commit)."""
        cursor = self._conn.cursor()
        cursor.executemany(query, params_list)
        return cursor

    def fetchone(self, query: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        """Execute query and fetch one result."""
        cursor = self._conn.cursor()
        cursor.execute(query, params)
        return cursor.fetchone()

    def fetchall(self, query: str, params: tuple = ()) -> list:
        """Execute query and fetch all results."""
        cursor = self._conn.cursor()
        cursor.execute(query, params)
        return cursor.fetchall()


# Global database instance (initialized lazily)
_db_instance: Optional[DatabaseService] = None


def get_database() -> DatabaseService:
    """Get the global database instance"""
    global _db_instance
    if _db_instance is None:
        from app.config import DATABASE_PATH
        _db_instance = DatabaseService(DATABASE_PATH)
    return _db_instance


def init_database(db_path: str) -> DatabaseService:
    """Initialize the database with a specific path"""
    global _db_instance
    _db_instance = DatabaseService(db_path)
    return _db_instance
