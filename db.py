import sqlite3
import os
import json
import uuid
from datetime import datetime

DB_FILE = "customer_care.db"

def init_db():
    """Initialize the database and create tables if they don't exist."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS support_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone_no TEXT NOT NULL,
            issue_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone_no TEXT NOT NULL,
            account_number TEXT NOT NULL UNIQUE,
            account_status TEXT NOT NULL,
            current_plan TEXT NOT NULL,
            plan_expiry TEXT NOT NULL,
            email TEXT,
            address TEXT,
            service_type TEXT,
            kyc_verified BOOLEAN,
            data_balance TEXT,
            billing_cycle TEXT
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS faq (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            issue TEXT NOT NULL,
            solution TEXT NOT NULL
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stream_id TEXT NOT NULL,
            phone_no TEXT NOT NULL,
            sender TEXT NOT NULL,
            message TEXT NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone_no TEXT NOT NULL,
            fact_description TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS customer_issues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone_no TEXT NOT NULL,
            issue_summary TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')


    
    # Seed mock data if empty
    cursor.execute('SELECT COUNT(*) FROM customers')
    if cursor.fetchone()[0] == 0:
        customers = [
            ('sukesh', '+919600944093', 'ACC-123-4567', 'Active', 'Unlimited 5G', '2026-12-31', 'sukesh@example.com', '123 Main St, Chennai, TN', 'Prepaid', True, '12.5 GB', 'June 2026'),
            ('john', '+1234567890', 'ACC-987-6543', 'Inactive', 'Basic 4G', '2023-01-01', 'john@example.com', '456 Elm St, New York, NY', 'Postpaid', False, '0 GB', 'Jan 2023')
        ]
        cursor.executemany('''
            INSERT INTO customers (name, phone_no, account_number, account_status, current_plan, plan_expiry, email, address, service_type, kyc_verified, data_balance, billing_cycle)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', customers)
        
    cursor.execute('SELECT COUNT(*) FROM faq')
    if cursor.fetchone()[0] == 0:
        faqs = [
            ('mobile network problem, no signal', 'To solve network issues, please turn on airplane mode for 10 seconds and turn it off. If the problem persists, restart your phone and check if your SIM card is inserted properly. Also check if there is an outage in your area.'),
            ('call outbound is not working, cannot make calls', 'If outbound calls are not working, check if your account is active and you have a valid recharge plan. Also ensure you are not dialing a restricted number and your phone is not in airplane mode.'),
            ('internet is slow, data not working', 'For slow internet, check your data balance. If you have data, try switching your preferred network type to 5G/4G in settings. You can also try resetting your network settings.')
        ]
        cursor.executemany('''
            INSERT INTO faq (issue, solution)
            VALUES (?, ?)
        ''', faqs)

    conn.commit()
    conn.close()

# --- Core DB Functions ---

def save_user_fact(phone_no: str, fact: str) -> bool:
    """Save a key fact or preference about a user for long term memory."""
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('INSERT INTO user_memory (phone_no, fact_description) VALUES (?, ?)', (phone_no, fact))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Error saving memory fact: {e}")
        return False

def get_user_facts(phone_no: str) -> list:
    """Retrieve all long-term memory facts for a user."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT fact_description FROM user_memory WHERE phone_no = ? ORDER BY created_at ASC', (phone_no,))
    rows = cursor.fetchall()
    conn.close()
    return [row[0] for row in rows]

def log_customer_issue(phone_no: str, issue_summary: str) -> bool:
    """Save a summary of a customer issue when a conversation ends."""
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('INSERT INTO customer_issues (phone_no, issue_summary) VALUES (?, ?)', (phone_no, issue_summary))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Error saving customer issue: {e}")
        return False

def get_customer_issues(phone_no: str) -> list:
    """Retrieve all past issues faced by the customer."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT issue_summary, created_at FROM customer_issues WHERE phone_no = ? ORDER BY created_at ASC', (phone_no,))
    rows = cursor.fetchall()
    conn.close()
    return [f"[{row[1][:10]}] {row[0]}" for row in rows]

def create_support_request(name: str, phone_no: str, issue_type: str) -> int:
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('INSERT INTO support_requests (name, phone_no, issue_type, created_at) VALUES (?, ?, ?, ?)',
                   (name, phone_no, issue_type, datetime.now()))
    request_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return request_id

def update_request_status(request_id: int, status: str):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('UPDATE support_requests SET status = ? WHERE id = ?', (status, request_id))
    conn.commit()
    conn.close()

def get_request_by_id(request_id: int) -> dict:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM support_requests WHERE id = ?', (request_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def save_chat_message(stream_id: str, phone_no: str, sender: str, message: str):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('INSERT INTO chat_messages (stream_id, phone_no, sender, message) VALUES (?, ?, ?, ?)',
                   (stream_id, phone_no, sender, message))
    conn.commit()
    conn.close()

def get_chat_history() -> list:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    # Fetch all chats ordered by timestamp descending so the newest sessions are first,
    # but we might group them later in the UI.
    cursor.execute('SELECT * FROM chat_messages ORDER BY timestamp ASC')
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_phone_by_stream_id(stream_id: str) -> str:
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT phone_no FROM chat_messages WHERE stream_id = ? LIMIT 1', (stream_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else "Unknown"

def get_customer_by_phone(phone_no: str) -> dict:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM customers WHERE phone_no = ?', (phone_no,))
    row = cursor.fetchone()
    if not row:
        cursor.execute('SELECT * FROM customers WHERE phone_no LIKE ?', (f'%{phone_no}%',))
        row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def get_customer_by_email(email: str) -> dict:
    if not email:
        return None
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    normalized_email = email.strip().lower()
    cursor.execute('SELECT * FROM customers WHERE lower(email) = ?', (normalized_email,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def get_customer_by_identifier(identifier: str) -> dict:
    if not identifier:
        return None
    identifier = identifier.strip()
    if "@" in identifier:
        return get_customer_by_email(identifier)
    if identifier.upper().startswith("ACC-"):
        return get_customer_by_account(identifier.upper())
    return get_customer_by_phone(identifier)

def get_customer_by_account(account_number: str) -> dict:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM customers WHERE account_number = ?', (account_number,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def search_faq(query: str) -> str:
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    keywords = query.lower().split()
    query_conditions = " OR ".join(["issue LIKE ?"] * len(keywords))
    params = [f"%{word}%" for word in keywords]
    cursor.execute(f'SELECT solution FROM faq WHERE {query_conditions}', params)
    results = cursor.fetchall()
    conn.close()
    if results:
        return " ".join([row[0] for row in results])
    return "I couldn't find a specific troubleshooting guide for that issue in the knowledge base."

# --- CRUD for Customer Management Portal ---

def get_all_customers() -> list:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM customers')
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def add_customer(data: dict) -> int:
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Ensure default values if missing
    email = data.get('email', '')
    address = data.get('address', '')
    service_type = data.get('service_type', 'Prepaid')
    kyc_verified = data.get('kyc_verified', False)
    data_balance = data.get('data_balance', '0 GB')
    billing_cycle = data.get('billing_cycle', '')

    cursor.execute('''
        INSERT INTO customers (name, phone_no, account_number, account_status, current_plan, plan_expiry, email, address, service_type, kyc_verified, data_balance, billing_cycle)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (data['name'], data['phone_no'], data['account_number'], data['account_status'], data['current_plan'], data['plan_expiry'], email, address, service_type, kyc_verified, data_balance, billing_cycle))
    customer_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return customer_id

def update_customer(customer_id: int, data: dict):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    email = data.get('email', '')
    address = data.get('address', '')
    service_type = data.get('service_type', 'Prepaid')
    kyc_verified = data.get('kyc_verified', False)
    data_balance = data.get('data_balance', '0 GB')
    billing_cycle = data.get('billing_cycle', '')

    cursor.execute('''
        UPDATE customers SET name=?, phone_no=?, account_number=?, account_status=?, current_plan=?, plan_expiry=?, email=?, address=?, service_type=?, kyc_verified=?, data_balance=?, billing_cycle=? WHERE id=?
    ''', (data['name'], data['phone_no'], data['account_number'], data['account_status'], data['current_plan'], data['plan_expiry'], email, address, service_type, kyc_verified, data_balance, billing_cycle, customer_id))
    conn.commit()
    conn.close()

def delete_customer(customer_id: int):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM customers WHERE id = ?', (customer_id,))
    conn.commit()
    conn.close()

# ============================================================
# AI TOOLS (Mock Implementations)
# ============================================================

# --- Account & Profile ---

def get_customer_profile(phone_number: str) -> str:
    customer = get_customer_by_phone(phone_number)
    if customer:
        return json.dumps({
            "name": customer["name"], "phone_no": customer["phone_no"],
            "account_number": customer["account_number"], "account_status": customer["account_status"],
            "current_plan": customer["current_plan"], "plan_expiry": customer["plan_expiry"],
            "email": customer.get("email"), "address": customer.get("address"),
            "service_type": customer.get("service_type", "Prepaid"),
            "kyc_verified": customer.get("kyc_verified", True),
            "billing_cycle": customer.get("billing_cycle", "N/A"),
            "previous_tickets": []
        })
    return json.dumps({"error": "Customer not found"})

def check_account_status(phone_number: str) -> str:
    customer = get_customer_by_phone(phone_number)
    if customer:
        return json.dumps({"status": customer["account_status"], "kyc_verified": customer.get("kyc_verified", True)})
    return json.dumps({"error": "Account not found"})

def check_active_plan(phone_number: str) -> str:
    customer = get_customer_by_phone(phone_number)
    if customer:
        return json.dumps({"plan_name": customer["current_plan"], "expiry": customer["plan_expiry"],
            "data_remaining": customer.get("data_balance", "0 GB"), "voice_remaining_mins": "Unlimited"})
    return json.dumps({"error": "Account not found"})

# --- Network ---

def check_network_status(phone_number: str) -> str:
    return json.dumps({"status": "Stable", "4g_coverage": True, "5g_coverage": True, "congestion": "Low"})

def check_tower_status(location: str) -> str:
    return json.dumps({"tower_id": "TWR-4521", "status": "Operational", "load": "62%", "location": location})

def check_outage(location: str) -> str:
    if "chennai" in location.lower() or "downtown" in location.lower():
        return json.dumps({"outage_detected": True, "estimated_resolution_time": "2 hours", "affected_area": location})
    return json.dumps({"outage_detected": False, "affected_area": location})

def check_signal_quality(phone_number: str) -> str:
    return json.dumps({"signal_strength": "-78 dBm", "quality": "Good", "band": "Band 40 (2300 MHz)", "technology": "4G LTE"})

# --- Voice & SMS ---

def check_voice_service(phone_number: str) -> str:
    return json.dumps({"voice_service": "Active", "volte_enabled": True, "call_forwarding": "Disabled"})

def check_sms_service(phone_number: str) -> str:
    return json.dumps({"sms_service": "Active", "international_sms": "Disabled", "message_center": "+919876543210"})

def check_message_center(phone_number: str) -> str:
    return json.dumps({"message_center_number": "+919876543210", "status": "Configured", "protocol": "GSM"})

# --- Recharge & Payment ---

def check_recharge_history(phone_number: str) -> str:
    return json.dumps([
        {"date": "2026-05-10", "amount": 299, "status": "SUCCESS"},
        {"date": "2026-04-10", "amount": 299, "status": "SUCCESS"}
    ])

def verify_transaction(transaction_id: str) -> str:
    return json.dumps({"transaction_id": transaction_id, "status": "SUCCESS", "amount": 299, "date": "2026-06-09", "payment_method": "UPI"})

def check_payment_gateway(phone_number: str) -> str:
    return json.dumps({"gateway_status": "Operational", "last_transaction_status": "SUCCESS", "pending_transactions": 0})

# --- Billing ---

def check_billing(phone_number: str) -> str:
    return json.dumps({"current_bill_amount": 0, "due_date": "N/A", "last_payment_amount": 299, "additional_charges": 0})

def get_bill(phone_number: str) -> str:
    return json.dumps({"bill_amount": 499, "bill_date": "2026-06-01", "due_date": "2026-06-20", "status": "Unpaid",
        "breakdown": {"base_plan": 399, "taxes": 72, "additional_services": 28}})

def get_usage(phone_number: str) -> str:
    customer = get_customer_by_phone(phone_number)
    data_used = "2.5 GB"
    if customer and customer.get("data_balance"):
        data_used = customer.get("data_balance")
        
    return json.dumps({"data_used": data_used, "data_limit_gb": 30, "voice_used_mins": 120,
        "voice_limit_mins": "Unlimited", "sms_sent": 45, "sms_limit": 100, "billing_cycle": customer.get("billing_cycle", "N/A") if customer else "N/A"})

def get_charges(phone_number: str) -> str:
    return json.dumps({"charges": [
        {"description": "Base Plan - Unlimited 399", "amount": 399},
        {"description": "Value Added Service - Caller Tune", "amount": 28},
        {"description": "GST @18%", "amount": 72}
    ], "total": 499})

# --- SIM ---

def check_sim_status(phone_number: str) -> str:
    return json.dumps({"sim_status": "Active", "sim_type": "Physical", "blocked": False})

def activate_sim(phone_number: str) -> str:
    return json.dumps({"status": "Activated", "activation_time": "Immediate", "phone_number": phone_number})

def replace_sim(phone_number: str) -> str:
    request_id = f"SIM-{str(uuid.uuid4())[:6].upper()}"
    return json.dumps({"request_id": request_id, "status": "Replacement Requested", "estimated_delivery": "2-3 business days"})

def generate_esim(phone_number: str) -> str:
    return json.dumps({"status": "eSIM QR Generated", "delivery_method": "Email", "activation_time": "Within 15 minutes"})

# --- Broadband / Fiber ---

def check_router_status(customer_id: str) -> str:
    return json.dumps({"router_model": "Nokia G-2425G-A", "status": "Online", "uptime": "14 days", "firmware": "v3.2.1"})

def check_fiber_status(customer_id: str) -> str:
    return json.dumps({"fiber_status": "Active", "optical_power": "-18.5 dBm", "link_speed": "1 Gbps", "packet_loss": "0%"})

def check_installation_status(customer_id: str) -> str:
    return json.dumps({"installation_status": "Completed", "installation_date": "2026-05-15", "technician": "Tech-301"})

# --- Roaming ---

def check_roaming_status(phone_number: str) -> str:
    return json.dumps({"roaming_active": False, "international_roaming": "Not Activated", "last_roaming_location": None})

def check_partner_network(location: str) -> str:
    return json.dumps({"partner_networks": ["Vodafone", "T-Mobile"], "coverage": "Available", "location": location})

def check_roaming_pack(phone_number: str) -> str:
    return json.dumps({"active_roaming_pack": None, "available_packs": [
        {"name": "Asia Roaming 7-Day", "price": 599, "data": "2GB"},
        {"name": "Global Roaming 30-Day", "price": 1999, "data": "5GB"}
    ]})

# --- Ticketing & Escalation ---

def create_ticket(issue_details) -> str:
    ticket_id = f"TKT-{str(uuid.uuid4())[:8].upper()}"
    if isinstance(issue_details, str):
        try:
            issue_details = json.loads(issue_details)
        except Exception:
            issue_details = {"summary": issue_details}
    return json.dumps({"ticket_id": ticket_id, "status": "OPEN", "priority": issue_details.get("priority", "MEDIUM")})

def update_ticket(ticket_id: str, update_details: str) -> str:
    return json.dumps({"ticket_id": ticket_id, "status": "Updated", "update": update_details})

def schedule_engineer_visit(customer_id: str) -> str:
    return json.dumps({"scheduled_date": "Tomorrow", "time_slot": "10:00 AM - 12:00 PM", "status": "Confirmed"})

def escalate_to_human(ticket_id: str) -> str:
    return json.dumps({"status": "Escalated", "human_agent_assigned": "Agent-402", "queue_position": 2})

# Initialize the database when the module is imported
init_db()
