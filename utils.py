import os
import sqlite3
import time

DB_PATH = 'assets.db'
REQUIRED_DIRS = ['db', 'qr_codes', 'logs']

def ensure_env(retries: int = 3, delay: float = 0.5):
    """Ensure required directories and sqlite DB exist. Safe to call at startup."""
    for d in REQUIRED_DIRS:
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass

conn = sqlite3.connect(DB_PATH, check_same_thread=False)
  # 1. Papkalarni va fayllarni tayyorlash (16-qatordan boshlab joyla)
import os
import sqlite3

# Papkalarni yaratish
for folder in ['logs', 'data']:
    if not os.path.exists(folder):
        os.makedirs(folder)

# Audit faylini yaratish
open(os.path.join('logs', 'audit.log'), 'a').close()

# Ma'lumotlar bazasiga ulanish
DB_PATH = os.path.join('data', 'assets.db')
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
# (Shu yerda kodning davomi ketaveradi...)
            conn.commit()
            conn.close()
            break
        except sqlite3.OperationalError:
            attempt += 1
            time.sleep(delay)
        except Exception:
            attempt += 1
            time.sleep(delay)

    # If still not created, raise to surface the issue
    if not os.path.exists(DB_PATH):
        # try one final time to create an empty file
        try:
            open(DB_PATH, 'a').close()
        except Exception:
            raise


def append_audit_log(entry: dict):
    """Append an audit entry (dict) as a JSON line to logs/audit.log"""
    import json
    try:
        path = os.path.join('logs', 'audit.log')
        with open(path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def save_qr_image(asset_id: str, bio) -> str:
    """Save BytesIO image to qr_codes/{asset_id}.png and return path."""
    try:
        from PIL import Image
        path = os.path.join('qr_codes', f"{asset_id}.png")
        # bio may be BytesIO or PIL Image
        if hasattr(bio, 'read'):
            with open(path, 'wb') as f:
                f.write(bio.getvalue())
            return path
        elif isinstance(bio, Image.Image):
            bio.save(path, 'PNG')
            return path
    except Exception:
        pass
    return ''


if __name__ == '__main__':
    ensure_env()
