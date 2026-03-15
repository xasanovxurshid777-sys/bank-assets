import streamlit as st
import sqlite3
import uuid
import datetime
import pandas as pd
from dataclasses import dataclass
from typing import Optional, List
import qrcode
from io import BytesIO
import hashlib
import os
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import LabelEncoder
import joblib
import plotly.express as px
import streamlit_authenticator as stauth
from yaml import safe_load
from utils import ensure_env, append_audit_log, save_qr_image
from src.ai.risk import train_risk_model as ai_train_risk_model, compute_risk_from_asset

risk_model = None
le_type = None
le_category = None

# Ensure runtime environment (folders, DB file) exists to avoid sqlite errors
ensure_env()

# Data Models
@dataclass
class Asset:
    id: str
    name: str
    type: str
    category: str  # IT, Office, Security
    serial: str
    status: str
    owner: str
    purchase_date: str  # YYYY-MM-DD

@dataclass
class AuditLog:
    id: str
    asset_id: str
    old_status: str
    new_status: str
    changed_by: str
    timestamp: str

# Valid statuses
VALID_STATUSES = ['REGISTERED', 'ASSIGNED', 'IN_REPAIR', 'LOST', 'WRITTEN_OFF']
BLOCKED_STATUSES = ['LOST', 'WRITTEN_OFF']

# DB Path'
DB_PATH = 'assets.db'

# Init DB
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Assets table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS assets (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            category TEXT NOT NULL,
            serial TEXT UNIQUE NOT NULL,
            status TEXT NOT NULL,
            owner TEXT,
            purchase_date TEXT
        )
    ''')
    
    # Audit logs table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS audit_logs (
            id TEXT PRIMARY KEY,
            asset_id TEXT NOT NULL,
            old_status TEXT NOT NULL,
            new_status TEXT NOT NULL,
            changed_by TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            FOREIGN KEY (asset_id) REFERENCES assets (id)
        )
    ''')
    
    conn.commit()
    conn.close()

# Backend functions
def create_asset(name: str, type_: str, category: str, serial: str, owner: str, purchase_date: str, changed_by: str) -> str:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    asset_id = str(uuid.uuid4())
    status = 'REGISTERED'
    
    try:
        cursor.execute('''
            INSERT INTO assets (id, name, type, category, serial, status, owner, purchase_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (asset_id, name, type_, category, serial, status, owner, purchase_date))
        
        # Audit log
        log_id = str(uuid.uuid4())
        cursor.execute('''
            INSERT INTO audit_logs (id, asset_id, old_status, new_status, changed_by, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (log_id, asset_id, 'NONE', status, changed_by, datetime.datetime.now().isoformat()))
        
        conn.commit()
        conn.close()
        # Append to file-based audit log for easy review
        try:
            append_audit_log({
                'id': log_id,
                'asset_id': asset_id,
                'old_status': 'NONE',
                'new_status': status,
                'changed_by': changed_by,
                'timestamp': datetime.datetime.now().isoformat()
            })
        except Exception:
            pass

        # generate and persist QR image
        try:
            asset = Asset(asset_id, name, type_, category, serial, status, owner, purchase_date)
            generate_qr(asset)
        except Exception:
            pass
        return asset_id
    except sqlite3.IntegrityError:
        conn.close()
        raise ValueError("Serial raqam allaqachon mavjud!")

def get_all_assets() -> List[Asset]:
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM assets", conn)
    conn.close()
    assets = []
    for _, row in df.iterrows():
        assets.append(Asset(
            id=row['id'],
            name=row['name'],
            type=row['type'],
            category=row['category'],
            serial=row['serial'],
            status=row['status'],
            owner=row['owner'],
            purchase_date=row['purchase_date']
        ))
    return assets

def update_status(asset_id: str, new_status: str, changed_by: str) -> bool:
    if new_status not in VALID_STATUSES:
        raise ValueError("Noto'g'ri status!")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Joriy statusni olish
    cursor.execute("SELECT status, owner FROM assets WHERE id=?", (asset_id,))
    result = cursor.fetchone()
    if not result:
        conn.close()
        raise ValueError("Aktiv topilmadi!")
    
    old_status, current_owner = result
    
    # Business rules: LOST/WRITTEN_OFF bo'lsa assign blok (owner o'zgartirish taqiq)
    if new_status == 'ASSIGNED' and old_status in BLOCKED_STATUSES:
        conn.close()
        raise ValueError("LOST yoki WRITTEN_OFF aktivni qayta assign qilib bo'lmaydi!")
    
    # Update
    cursor.execute("UPDATE assets SET status=? WHERE id=?", (new_status, asset_id))
    
    # Audit log
    log_id = str(uuid.uuid4())
    cursor.execute('''
        INSERT INTO audit_logs (id, asset_id, old_status, new_status, changed_by, timestamp)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (log_id, asset_id, old_status, new_status, changed_by, datetime.datetime.now().isoformat()))
    
    conn.commit()
    conn.close()
    # Append to file-based audit log and update QR image
    try:
        append_audit_log({
            'id': log_id,
            'asset_id': asset_id,
            'old_status': old_status,
            'new_status': new_status,
            'changed_by': changed_by,
            'timestamp': datetime.datetime.now().isoformat()
        })
    except Exception:
        pass
    try:
        # refresh QR
        conn2 = sqlite3.connect(DB_PATH)
        df = pd.read_sql_query('SELECT * FROM assets WHERE id=?', conn2, params=(asset_id,))
        conn2.close()
        if not df.empty:
            row = df.iloc[0]
            asset = Asset(row['id'], row['name'], row['type'], row['category'], row['serial'], row['status'], row['owner'], row['purchase_date'])
            generate_qr(asset)
    except Exception:
        pass
    return True

def get_audit_logs(asset_id: str) -> List[AuditLog]:
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM audit_logs WHERE asset_id=? ORDER BY timestamp DESC", conn, params=(asset_id,))
    conn.close()
    logs = []
    for _, row in df.iterrows():
        logs.append(AuditLog(
            id=row['id'],
            asset_id=row['asset_id'],
            old_status=row['old_status'],
            new_status=row['new_status'],
            changed_by=row['changed_by'],
            timestamp=row['timestamp']
        ))
    return logs

def generate_qr(asset: Asset) -> BytesIO:
    qr_data = f"ID:{asset.id}|Status:{asset.status}"
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(qr_data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    bio = BytesIO()
    img.save(bio, 'PNG')
    bio.seek(0)
    # Save PNG to disk for persistent QR
    try:
        save_qr_image(asset.id, bio)
        bio.seek(0)
    except Exception:
        pass
    return bio


def compute_risk(asset: Asset) -> tuple[int, str, float]:
    # Wrapper around AI module
    try:
        return compute_risk_from_asset(asset.purchase_date, asset.type, asset.category)
    except Exception:
        return 0, 'Xato', 0.0

def train_risk_model():
    # call AI module trainer
    try:
        ai_train_risk_model()
    except Exception:
        pass

# Streamlit App
def app_content():
    st.set_page_config(page_title="Bank Assets Smart Governance", layout="wide")
    st.title("🏦 Bank Aktivlari Aqlli Boshqarish Tizimi")
    
    init_db()  # DB init
    train_risk_model()
    
    # Sidebar
    st.sidebar.header("Navigatsiya")

def main():
    # Load config if present; fall back to no-auth mode when missing or invalid
    auth_enabled = False
    try:
        with open('config.yaml') as file:
            config = safe_load(file)
        authenticator = stauth.Authenticate(
            config['credentials'],
            config['cookie']['name'],
            config['cookie']['key'],
            config['cookie']['expiry_days']
        )
        auth_enabled = True
    except Exception:
        auth_enabled = False

    if auth_enabled:
        login_res = authenticator.login(location='main')
        if not login_res:
            # If the authenticator returns None (e.g., not rendered), fall back to no-auth
            st.sidebar.title('Xush kelibsiz Admin (no-auth)')
            name = 'Admin'
            app_content()
        else:
            name, authentication_status, username = login_res

            if authentication_status == False:
                st.error('Login/parol noto\'g\'ri')
                return
            elif authentication_status == None:
                st.warning('Iltimos login/parol kiriting')
                return
            elif authentication_status:
                authenticator.logout('Chiqish', location='sidebar')
                st.sidebar.title(f'Xush kelibsiz *{name}*')
                app_content()
    else:
        # No config: run in single-user admin mode
        st.sidebar.title('Xush kelibsiz Admin (no-auth)')
        name = 'Admin'
        app_content()
    page = st.sidebar.selectbox("Sahifa", ["Dashboard", "Yangi Aktiv Qo'shish", "Status O'zgartirish", "Audit Logs"])
    
    if page == "Dashboard":
        st.header("📊 Inventory Dashboard")
        assets = get_all_assets()
        if assets:
            df = pd.DataFrame([{
                'ID': a.id,
                'Nomi': a.name,
                'Turi': a.type,
                'Kategoriya': a.category,
                'Serial': a.serial,
                'Status': a.status,
                'Egasi': a.owner,
                'Xarid Sana': a.purchase_date,
                'Aging (kun)': compute_risk(a)[0],
                'Risk Darajasi': compute_risk(a)[1],
'Risk Ehtimoll (%)': f"{compute_risk(a)[2]*100:.1f}%"
            } for a in assets])
            st.dataframe(df, use_container_width=True)
            
            # 🤖 AI Bashorat Ko'rsatkichlari
            if assets:
                risks = [compute_risk(a) for a in assets]
                aging_days_list, risk_levels_list, risk_scores_list = zip(*risks)
                avg_risk = sum(risk_scores_list) / len(risk_scores_list)
                high_risk_count = sum(1 for score in risk_scores_list if score > 0.7)
                avg_aging = sum(aging_days_list) / len(aging_days_list)
                
                col1, col2, col3 = st.columns(3)
                col1.metric("O'rtacha Risk Ehtimoll (%)", f"{avg_risk*100:.1f}%")
                col2.metric("Yuqori Risk Aktivlar", high_risk_count)
                col3.metric("O'rtacha Aging (kun)", f"{int(avg_aging)}")
            
            # Filter/Search
            search = st.text_input("Qidirish")
            if search:
                filtered = df[df.apply(lambda row: row.astype(str).str.contains(search, case=False).any(), axis=1)]
                st.dataframe(filtered)
        else:
            st.info("Hozircha aktivlar yo'q. Yangi qo'shing!")
        
        # Test data button
        if st.button("Test Ma'lumotlar Qo'shish"):
            create_asset("Dell Laptop", "Laptop", "IT", "SN12345", "IT Bo'lim", "2024-01-01", "Admin")
            create_asset("HP Printer", "Printer", "Office", "SN67890", None, "2024-02-01", "Admin")
            st.success("Test aktivlar qo'shildi!")
            st.rerun()
    
    elif page == "Yangi Aktiv Qo'shish":
        st.header("➕ Yangi Aktiv Ro'yxatdan O'tkazish")
        with st.form("new_asset"):
            name = st.text_input("Nomi")
            type_ = st.text_input("Turi")
            category = st.selectbox("Kategoriya", ["IT", "Office", "Security"])
            serial = st.text_input("Seriya Raqami")
            owner = st.text_input("Egasi (xodim/bo'lim)")
            purchase_date = st.date_input("Xarid Sana", value=datetime.date.today())
            changed_by = st.text_input("O'zgartirgan", value="Admin")
            submit = st.form_submit_button("Qo'shish")
        
        if submit:
            try:
                asset_id = create_asset(name, type_, category, serial, owner, purchase_date.strftime('%Y-%m-%d'), changed_by)
                st.success(f"Aktiv qo'shildi! ID: {asset_id}")
                st.rerun()
            except ValueError as e:
                st.error(str(e))
    
    elif page == "Status O'zgartirish":
        st.header("🔄 Aktiv Lifecycle - Status O'zgartirish")
        assets = get_all_assets()
        if assets:
            asset_names = {a.name: a for a in assets}
            selected_name = st.selectbox("Aktiv tanlang", list(asset_names.keys()))
            asset = asset_names[selected_name]
            
            st.info(f"Joriy status: **{asset.status}**")
            
            col1, col2 = st.columns(2)
            with col1:
                new_status = st.selectbox("Yangi Status", VALID_STATUSES, index=VALID_STATUSES.index(asset.status))
            with col2:
                changed_by = st.text_input("O'zgartirgan", value="Admin")
            
            if st.button("Statusni O'zgartirish"):
                try:
                    update_status(asset.id, new_status, changed_by)
                    st.success("Status o'zgartirildi!")
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))
            
            # QR
            st.subheader("📱 QR Kod")
            qr_bio = generate_qr(asset)
            st.image(qr_bio, caption=f"ID: {asset.id}, Status: {asset.status}")
        else:
            st.warning("Avval aktivlar qo'shing!")
    
    elif page == "Audit Logs":
        st.header("📋 Audit Logs")
        asset_id = st.text_input("Aktiv ID (ixtiyoriy)")
        logs = []
        if asset_id:
            logs = get_audit_logs(asset_id)
        else:
            conn = sqlite3.connect(DB_PATH)
            df = pd.read_sql_query("SELECT * FROM audit_logs ORDER BY timestamp DESC", conn)
            conn.close()
            for _, row in df.iterrows():
                logs.append(AuditLog(**row))
        
        if logs:
            df = pd.DataFrame([{
                'Aktiv ID': log.asset_id,
                'Eski Status': log.old_status,
                'Yangi Status': log.new_status,
                'O\'zgartirgan': log.changed_by,
                'Vaqt': log.timestamp
            } for log in logs])
            st.dataframe(df)
        else:
            st.info("Loglar yo'q.")

if __name__ == "__main__":
    main()

