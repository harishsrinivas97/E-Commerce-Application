from flask import Flask, render_template, request, redirect, session, flash, jsonify, url_for, make_response
from flask_mail import Mail, Message
import sqlite3
import re
import bcrypt
import random
import config
import os
import time
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
import razorpay
import traceback
from decimal import Decimal

# PDF invoice dependencies
from io import BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                Paragraph, Spacer, HRFlowable)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT


app = Flask(__name__)

# Set secret key for session management
app.secret_key = config.SECRET_KEY

# ---------------- EMAIL CONFIGURATION ----------------
app.config['MAIL_SERVER'] = config.MAIL_SERVER
app.config['MAIL_PORT'] = config.MAIL_PORT
app.config['MAIL_USE_TLS'] = config.MAIL_USE_TLS
app.config['MAIL_USERNAME'] = config.MAIL_USERNAME
app.config['MAIL_PASSWORD'] = config.MAIL_PASSWORD

mail = Mail(app)


# Context Processor to inject cart_count and user profile image globally across all templates
@app.context_processor
def inject_cart_count():
    cart = session.get('cart', {})
    count = 0
    if isinstance(cart, dict):
        try:
            count = sum(int(item.get('quantity', 1)) for item in cart.values())
        except Exception:
            count = len(cart)

    user_img = session.get('user_profile_image')
    if not user_img and session.get('user_id'):
        try:
            conn = get_db_connection()
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT profile_image FROM users WHERE user_id = %s", (session['user_id'],))
            row = cur.fetchone()
            cur.close()
            conn.close()
            if row and row.get('profile_image'):
                user_img = row['profile_image']
                session['user_profile_image'] = user_img
        except Exception:
            pass

    return dict(cart_count=count, current_user_image=user_img)


# SQLite Database Connection Setup & Compatibility Layer
def dict_factory(cursor, row):
    fields = [col[0] for col in cursor.description]
    return {col: val for col, val in zip(fields, row)}


class SQLiteCursorWrapper:
    def __init__(self, raw_cursor):
        self._cursor = raw_cursor

    def execute(self, query, params=None):
        if params is not None:
            if "%s" in query:
                query = re.sub(r'(?<!%)%s', '?', query)
            if isinstance(params, list):
                params = tuple(params)
            return self._cursor.execute(query, params)
        else:
            return self._cursor.execute(query)

    def executemany(self, query, seq_of_params):
        if "%s" in query:
            query = re.sub(r'(?<!%)%s', '?', query)
        return self._cursor.executemany(query, seq_of_params)

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def fetchmany(self, size=None):
        return self._cursor.fetchmany(size) if size is not None else self._cursor.fetchmany()

    @property
    def lastrowid(self):
        return self._cursor.lastrowid

    @property
    def rowcount(self):
        return self._cursor.rowcount

    @property
    def description(self):
        return self._cursor.description

    def close(self):
        try:
            self._cursor.close()
        except Exception:
            pass

    def __iter__(self):
        return iter(self._cursor)


class SQLiteConnectionWrapper:
    def __init__(self, raw_conn):
        self._conn = raw_conn
        self._conn.row_factory = dict_factory

    def cursor(self, *args, **kwargs):
        # Ignores dictionary=True as dict_factory is already active
        raw_cur = self._conn.cursor()
        return SQLiteCursorWrapper(raw_cur)

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass

    def executescript(self, script):
        return self._conn.executescript(script)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.rollback()
        else:
            self.commit()
        self.close()


def get_db_connection():
    """
    Creates and returns a connection to the SQLite database.
    Enforces thread safety, foreign key constraints, and dictionary row factory.
    """
    db_path = getattr(config, 'DB_PATH', os.path.join(os.path.abspath(os.path.dirname(__file__)), "Database", "smartcart.db"))
    raw_conn = sqlite3.connect(db_path, timeout=30.0, check_same_thread=False)
    raw_conn.execute("PRAGMA foreign_keys = ON;")
    return SQLiteConnectionWrapper(raw_conn)


def init_db():
    """Initializes the SQLite database with all tables if they do not exist."""
    db_path = getattr(config, 'DB_PATH', os.path.join(os.path.abspath(os.path.dirname(__file__)), "Database", "smartcart.db"))
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    # Ensure upload directories exist
    base_dir = os.path.abspath(os.path.dirname(__file__))
    os.makedirs(os.path.join(base_dir, 'static', 'uploads', 'admin_profiles'), exist_ok=True)
    os.makedirs(os.path.join(base_dir, 'static', 'uploads', 'user_profiles'), exist_ok=True)
    os.makedirs(os.path.join(base_dir, 'static', 'uploads', 'product_images'), exist_ok=True)

    conn = get_db_connection()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS admin (
        admin_id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        email TEXT UNIQUE,
        password TEXT,
        profile_image TEXT
    );

    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        phone TEXT NOT NULL,
        password TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        profile_image TEXT
    );

    CREATE TABLE IF NOT EXISTS products (
        product_id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        description TEXT,
        category TEXT,
        price REAL,
        image TEXT,
        admin_id INTEGER REFERENCES admin(admin_id)
    );

    CREATE TABLE IF NOT EXISTS orders (
        order_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        razorpay_order_id TEXT UNIQUE NOT NULL,
        razorpay_payment_id TEXT UNIQUE NOT NULL,
        razorpay_signature TEXT NOT NULL,
        total_amount REAL NOT NULL,
        currency TEXT DEFAULT 'INR',
        payment_status TEXT DEFAULT 'PAID',
        order_status TEXT DEFAULT 'PLACED',
        full_name TEXT NOT NULL,
        phone TEXT NOT NULL,
        address_line TEXT NOT NULL,
        city TEXT NOT NULL,
        state TEXT NOT NULL,
        pincode TEXT NOT NULL,
        landmark TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS order_items (
        order_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
        product_id INTEGER NOT NULL,
        product_name TEXT NOT NULL,
        product_image TEXT,
        price REAL NOT NULL,
        quantity INTEGER NOT NULL,
        subtotal REAL NOT NULL
    );
    """)
    conn.commit()
    conn.close()

# Auto-initialize SQLite database schema on startup
init_db()


# Home Route (First Flask Route)
@app.route('/')
def home():
    featured_products = []
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT product_id, name, description, category, price, image
            FROM products
            ORDER BY product_id DESC
            LIMIT 8
        """)
        featured_products = cursor.fetchall()
        cursor.close()
        conn.close()
    except Exception as e:
        print("Home products fetch error:", e)

    return render_template("index.html", featured_products=featured_products)


# ---------------------------------------------------------
# ROUTE 1: ADMIN SIGNUP (SEND OTP)
# ---------------------------------------------------------
@app.route('/admin-signup', methods=['GET', 'POST'])
def admin_signup():

    # Show form
    if request.method == "GET":
        return render_template("admin/admin_signup.html")

    # POST â†’ Process signup
    name = request.form['name']
    email = request.form['email']

    # 1ï¸âƒ£ Check if admin email already exists
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT admin_id FROM admin WHERE email=%s", (email,))
    existing_admin = cursor.fetchone()
    cursor.close()
    conn.close()

    if existing_admin:
        flash("This email is already registered. Please login instead.", "danger")
        return redirect('/admin-signup')

    # 2ï¸âƒ£ Save user input temporarily in session
    session['signup_name'] = name
    session['signup_email'] = email

    # 3ï¸âƒ£ Generate OTP and store in session
    otp = random.randint(100000, 999999)
    session['otp'] = otp

    # 4ï¸âƒ£ Send OTP Email
    message = Message(
        subject="SmartCart Admin OTP",
        sender=config.MAIL_USERNAME,
        recipients=[email]
    )
    message.body = f"Your OTP for SmartCart Admin Registration is: {otp}"
    mail.send(message)

    flash("OTP sent to your email!", "success")
    return redirect('/verify-otp')



# ---------------------------------------------------------
# ROUTE 2 & 3: DISPLAY OTP PAGE + VERIFY OTP + SAVE ADMIN
# ---------------------------------------------------------
@app.route('/verify-otp', methods=['GET', 'POST'])
def verify_otp_get():

    # GET -> Show OTP form
    if request.method == 'GET':
        return render_template("admin/verify_otp.html")

    # POST -> Validate OTP and register admin
    user_otp = request.form['otp']
    password = request.form['password']

    # Compare OTP
    if str(session.get('otp')) != str(user_otp):
        flash("Invalid OTP. Try again!", "danger")
        return redirect('/verify-otp')

    # Hash password using bcrypt
    hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())

    # Insert admin into database
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO admin (name, email, password) VALUES (%s, %s, %s)",
        (session['signup_name'], session['signup_email'], hashed_password)
    )
    conn.commit()
    cursor.close()
    conn.close()

    # Clear temporary session data
    session.pop('otp', None)
    session.pop('signup_name', None)
    session.pop('signup_email', None)

    flash("Admin Registered Successfully!", "success")
    return redirect('/admin-login')


# ROUTE 4: ADMIN LOGIN PAGE (GET + POST)
# =================================================================
@app.route('/admin-login', methods=['GET', 'POST'])
def admin_login():

    # Show login page
    if request.method == 'GET':
        return render_template("admin/admin_login.html")

    # POST â†’ Validate login
    email = request.form['email']
    password = request.form['password']

    # Step 1: Check if admin email exists
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT * FROM admin WHERE email=%s", (email,))
    admin = cursor.fetchone()

    cursor.close()
    conn.close()

    if admin is None:
        flash("Email not found! Please register first.", "danger")
        return redirect('/admin-login')

    # Step 2: Compare entered password with hashed password
    stored_hashed_password = (
    admin['password'].encode('utf-8')
    if isinstance(admin['password'], str)
    else admin['password']
    )

    if not bcrypt.checkpw(password.encode('utf-8'), stored_hashed_password):
        flash("Incorrect password! Try again.", "danger")
        return redirect('/admin-login')

    # Step 5: If login success â†’ Create admin session
    session['admin_id'] = admin['admin_id']
    session['admin_name'] = admin['name']
    session['admin_email'] = admin['email']
    session['admin_profile_image'] = admin.get('profile_image')

    flash("Login Successful!", "success")
    return redirect('/admin-dashboard')



# =================================================================
# ROUTE 5: ADMIN DASHBOARD (PROTECTED ROUTE)
# =================================================================
@app.route('/admin-dashboard')
def admin_dashboard():

    # Protect dashboard â†’ Only logged-in admin can access
    if 'admin_id' not in session:
        flash("Please login to access dashboard!", "danger")
        return redirect('/admin-login')

    # Gather dashboard statistics
    stats = {}
    recent_products = []
    try:
        admin_id = session['admin_id']
        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT COUNT(*) AS cnt FROM products WHERE admin_id=%s", (admin_id,))
        stats['total_products'] = cur.fetchone()['cnt']
        cur.execute("SELECT COUNT(*) AS cnt FROM users")
        stats['total_customers'] = cur.fetchone()['cnt']
        cur.execute("SELECT COUNT(*) AS cnt FROM orders")
        stats['total_orders'] = cur.fetchone()['cnt']
        cur.execute("SELECT COUNT(*) AS cnt FROM orders WHERE payment_status='PAID'")
        stats['paid_orders'] = cur.fetchone()['cnt']
        cur.execute("SELECT COALESCE(SUM(total_amount),0) AS rev FROM orders WHERE payment_status='PAID'")
        stats['total_revenue'] = cur.fetchone()['rev']
        cur.execute("SELECT * FROM products WHERE admin_id=%s ORDER BY product_id DESC LIMIT 5", (admin_id,))
        recent_products = cur.fetchall()
        cur.close()
        conn.close()
    except Exception as e:
        print("Dashboard stats error:", e)

    return render_template("admin/dashboard.html",
                           admin_name=session['admin_name'],
                           stats=stats,
                           recent_products=recent_products)



# =================================================================
# ROUTE 6: ADMIN LOGOUT
# =================================================================
@app.route('/admin-logout')
def admin_logout():

    # Clear admin session
    session.pop('admin_id', None)
    session.pop('admin_name', None)
    session.pop('admin_email', None)

    flash("Logged out successfully.", "success")
    return redirect('/admin-login')

# ------------------- IMAGE UPLOAD PATH -------------------
UPLOAD_FOLDER = 'static/uploads/product_images'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER


# =================================================================
# ROUTE 7: SHOW ADD PRODUCT PAGE (Protected Route)
# =================================================================
@app.route('/admin/add-item', methods=['GET'])
def add_item_page():

    # Only logged-in admin can access
    if 'admin_id' not in session:
        flash("Please login first!", "danger")
        return redirect('/admin-login')

    return render_template("admin/add_item.html")



# =================================================================
# ROUTE 8: ADD PRODUCT INTO DATABASE
# =================================================================
@app.route('/admin/add-item', methods=['POST'])
def add_item():

    # Check admin session
    if 'admin_id' not in session:
        flash("Please login first!", "danger")
        return redirect('/admin-login')

    # 1ï¸âƒ£ Get form data
    name = request.form['name']
    description = request.form['description']
    category = request.form['category']
    price = request.form['price']
    image_file = request.files['image']

    # 2ï¸âƒ£ Validate image upload
    if image_file.filename == "":
        flash("Please upload a product image!", "danger")
        return redirect('/admin/add-item')

    # 3ï¸âƒ£ Secure the file name
    filename = secure_filename(image_file.filename)

    # 4ï¸âƒ£ Create full path
    image_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)

    # 5ï¸âƒ£ Save image into folder
    image_file.save(image_path)

    # 6ï¸âƒ£ Insert product into database
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
    """INSERT INTO products
    (name, description, category, price, image, admin_id)
    VALUES (%s, %s, %s, %s, %s, %s)""",
    (name, description, category, price, filename, session['admin_id'])
    )

    conn.commit()
    cursor.close()
    conn.close()

    flash("Product added successfully!", "success")
    return redirect('/admin-dashboard')

#product view
@app.route('/admin/view-item/<int:item_id>')
def view_item(item_id):
    if 'admin_id' not in session:
        flash("Please login!", "danger")
        return redirect('/admin-login')

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT * FROM products WHERE product_id=%s AND admin_id=%s",
            (item_id, session['admin_id'])
        )
        product = cursor.fetchone()
    finally:
        cursor.close()
        conn.close()

    if not product:
        flash("Product not found!", "warning")
        return redirect('/admin/item-list')

    return render_template('admin/view_item.html', product=product)

# =================================================================
# ROUTE 9:UPDATED PRODUCT LIST WITH SEARCH + CATEGORY FILTER
# =================================================================
@app.route('/admin/item-list')
def item_list():

    if 'admin_id' not in session:
        flash("Please login!", "danger")
        return redirect('/admin-login')

    admin_id = session['admin_id']

    search = request.args.get('search', '')
    category_filter = request.args.get('category', '')

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # Fetch only logged-in admin categories
    cursor.execute(
        "SELECT DISTINCT category FROM products WHERE admin_id=%s",
        (admin_id,)
    )
    categories = cursor.fetchall()

    # Fetch only logged-in admin products
    query = "SELECT * FROM products WHERE admin_id=%s"
    params = [admin_id]

    if search:
        query += " AND name LIKE %s"
        params.append("%" + search + "%")

    if category_filter:
        query += " AND category=%s"
        params.append(category_filter)

    cursor.execute(query, params)
    products = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template(
        "admin/item_list.html",
        products=products,
        categories=categories
    )
# =================================================================
# ROUTE 11: SHOW UPDATE FORM WITH EXISTING DATA
# =================================================================
@app.route('/admin/update-item/<int:item_id>', methods=['GET'])
def update_item_page(item_id):

    # Check login
    if 'admin_id' not in session:
        flash("Please login!", "danger")
        return redirect('/admin-login')

    # Fetch product data
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute(
    "SELECT * FROM products WHERE product_id=%s AND admin_id=%s",
    (item_id, session['admin_id'])
    )
    product = cursor.fetchone()

    cursor.close()
    conn.close()

    if not product:
        flash("Product not found!", "danger")
        return redirect('/admin/item-list')

    return render_template("admin/update_item.html", product=product)

# =================================================================
# ROUTE-12: UPDATE PRODUCT + OPTIONAL IMAGE REPLACE
# =================================================================
@app.route('/admin/update-item/<int:item_id>', methods=['POST'])
def update_item(item_id):

    if 'admin_id' not in session:
        flash("Please login!", "danger")
        return redirect('/admin-login')

    # 1ï¸âƒ£ Get updated form data
    name = request.form['name']
    description = request.form['description']
    category = request.form['category']
    price = request.form['price']

    new_image = request.files['image']

    # 2ï¸âƒ£ Fetch old product data
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
    "SELECT * FROM products WHERE product_id=%s AND admin_id=%s",
    (item_id, session['admin_id']))

    product = cursor.fetchone()

    if not product: 
        cursor.close()
        conn.close()
        flash("Product not found or unauthorized!", "danger")
        return redirect('/admin/item-list')

    old_image_name = product['image']

    # 3ï¸âƒ£ If admin uploaded a new image â†’ replace it
    if new_image and new_image.filename != "":
        
        # Secure filename
        from werkzeug.utils import secure_filename
        new_filename = secure_filename(new_image.filename)

        # Save new image
        new_image_path = os.path.join(app.config['UPLOAD_FOLDER'], new_filename)
        new_image.save(new_image_path)

        # Delete old image file
        old_image_path = os.path.join(app.config['UPLOAD_FOLDER'], old_image_name)
        if os.path.exists(old_image_path):
            os.remove(old_image_path)

        final_image_name = new_filename

    else:
        # No new image uploaded â†’ keep old one
        final_image_name = old_image_name

    # 4ï¸âƒ£ Update product in the database
    cursor.execute("""
    UPDATE products
    SET name=%s, description=%s, category=%s, price=%s, image=%s
    WHERE product_id=%s AND admin_id=%s
    """, (
    name, description, category, price,
    final_image_name, item_id, session['admin_id']
    ))

    conn.commit()
    cursor.close()
    conn.close()

    flash("Product updated successfully!", "success")
    return redirect('/admin/item-list')

#delete Product
@app.route('/admin/delete-item/<int:item_id>')
def delete_item(item_id):

    if 'admin_id' not in session:
        flash("Please login!", "danger")
        return redirect('/admin-login')

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """DELETE FROM products
        WHERE product_id=%s AND admin_id=%s""",
        (item_id, session['admin_id'])
    )

    conn.commit()

    if cursor.rowcount == 0:
        flash("Product not found or unauthorized!", "danger")
    else:
        flash("Product deleted successfully!", "success")

    cursor.close()
    conn.close()

    return redirect('/admin/item-list')


ADMIN_UPLOAD_FOLDER = 'static/uploads/admin_profiles'
app.config['ADMIN_UPLOAD_FOLDER'] = ADMIN_UPLOAD_FOLDER

USER_UPLOAD_FOLDER = 'static/uploads/user_profiles'
app.config['USER_UPLOAD_FOLDER'] = USER_UPLOAD_FOLDER
os.makedirs(app.config['USER_UPLOAD_FOLDER'], exist_ok=True)


# =====================================================
# DISPLAY ADMIN PROFILE
# =====================================================

@app.route('/admin/profile', methods=['GET'])
def admin_profile():

    if 'admin_id' not in session:
        flash("Please login!", "danger")
        return redirect('/admin-login')

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT admin_id, name, email, profile_image
        FROM admin
        WHERE admin_id = %s
    """, (session['admin_id'],))

    admin = cursor.fetchone()

    cursor.close()
    conn.close()

    if not admin:
        flash("Admin account not found!", "danger")
        return redirect('/admin-login')

    session['admin_name'] = admin['name']
    session['admin_email'] = admin['email']
    session['admin_profile_image'] = admin.get('profile_image')

    return render_template(
        "admin/admin_profile.html",
        admin=admin
    )


# =====================================================
# SHOW EDIT ADMIN PROFILE PAGE
# =====================================================

@app.route('/admin/edit-profile', methods=['GET'])
def admin_edit_profile():

    if 'admin_id' not in session:
        flash("Please login!", "danger")
        return redirect('/admin-login')

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT admin_id, name, email, profile_image
        FROM admin
        WHERE admin_id = %s
    """, (session['admin_id'],))

    admin = cursor.fetchone()

    cursor.close()
    conn.close()

    if not admin:
        flash("Admin account not found!", "danger")
        return redirect('/admin-login')

    session['admin_name'] = admin['name']
    session['admin_email'] = admin['email']
    session['admin_profile_image'] = admin.get('profile_image')

    return render_template(
        "admin/admin_edit_profile.html",
        admin=admin
    )


# =====================================================
# UPDATE ADMIN PROFILE
# =====================================================

@app.route('/admin/profile', methods=['POST'])
def admin_profile_update():

    if 'admin_id' not in session:
        flash("Please login!", "danger")
        return redirect('/admin-login')

    admin_id = session['admin_id']

    name = request.form['name'].strip()
    email = request.form['email'].strip().lower()
    new_password = request.form.get('password', '').strip()
    new_image = request.files.get('profile_image')

    if not name or not email:
        flash("Name and email are required!", "danger")
        return redirect('/admin/edit-profile')

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # Fetch existing admin
    cursor.execute(
        "SELECT * FROM admin WHERE admin_id = %s",
        (admin_id,)
    )

    admin = cursor.fetchone()

    if not admin:
        cursor.close()
        conn.close()
        flash("Admin account not found!", "danger")
        return redirect('/admin-login')

    # Check whether another admin uses this email
    cursor.execute("""
        SELECT admin_id
        FROM admin
        WHERE email = %s AND admin_id != %s
    """, (email, admin_id))

    existing_admin = cursor.fetchone()

    if existing_admin:
        cursor.close()
        conn.close()
        flash("This email is already registered!", "danger")
        return redirect('/admin/edit-profile')

    # Keep existing password unless a new one is provided
    if new_password:
        hashed_password = bcrypt.hashpw(
            new_password.encode('utf-8'),
            bcrypt.gensalt()
        ).decode('utf-8')
    else:
        hashed_password = admin['password']

    old_image_name = admin.get('profile_image')
    final_image_name = old_image_name
    new_image_path = None

    # Process new profile image
    if new_image and new_image.filename:

        allowed_extensions = {'png', 'jpg', 'jpeg', 'webp'}

        filename = secure_filename(new_image.filename)
        extension = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''

        if extension not in allowed_extensions:
            cursor.close()
            conn.close()
            flash("Only PNG, JPG, JPEG and WEBP images are allowed!", "danger")
            return redirect('/admin/edit-profile')

        os.makedirs(app.config['ADMIN_UPLOAD_FOLDER'], exist_ok=True)

        final_image_name = (
            f"{admin_id}_{int(time.time())}_{filename}"
        )

        new_image_path = os.path.join(
            app.config['ADMIN_UPLOAD_FOLDER'],
            final_image_name
        )

        new_image.save(new_image_path)

    # Update database
    try:
        cursor.execute("""
            UPDATE admin
            SET name = %s,
                email = %s,
                password = %s,
                profile_image = %s
            WHERE admin_id = %s
        """, (
            name,
            email,
            hashed_password,
            final_image_name,
            admin_id
        ))

        conn.commit()

    except Exception:
        conn.rollback()

        if new_image_path and os.path.exists(new_image_path):
            os.remove(new_image_path)

        cursor.close()
        conn.close()

        flash("Unable to update profile. Please try again.", "danger")
        return redirect('/admin/edit-profile')

    cursor.close()
    conn.close()

    # Delete old image only after successful database update
    if new_image_path and old_image_name:
        old_image_path = os.path.join(
            app.config['ADMIN_UPLOAD_FOLDER'],
            old_image_name
        )

        if os.path.exists(old_image_path):
            os.remove(old_image_path)

    # Update session information
    session['admin_name'] = name
    session['admin_email'] = email
    if new_image_path:
        session['admin_profile_image'] = os.path.basename(new_image_path)

    flash("Admin profile updated successfully!", "success")
    return redirect('/admin/profile')


# =====================================================
# ADMIN FORGOT PASSWORD - SEND OTP
# =====================================================

@app.route('/admin/forgot', methods=['GET', 'POST'])
def Forgot():

    if request.method == 'POST':

        email = request.form['email']

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute(
            "SELECT admin_id FROM admin WHERE email=%s",
            (email,)
        )

        admin = cursor.fetchone()

        cursor.close()
        conn.close()

        if not admin:
            flash("Email not registered!", "danger")
            return redirect('/admin/forgot')

        # Generate OTP
        otp = random.randint(100000, 999999)

        session['reset_email'] = email
        session['reset_otp'] = str(otp)
        session['reset_otp_time'] = time.time()

        # Send OTP through Gmail
        message = Message(
            subject="SmartCart Admin Password Reset OTP",
            sender=config.MAIL_USERNAME,
            recipients=[email]
        )

        message.body = f"""
Hello Admin,

Your SmartCart password reset OTP is: {otp}

This OTP is valid for 5 minutes.

Do not share this OTP with anyone.
"""

        mail.send(message)

        flash("OTP sent successfully to your email!", "success")
        return redirect('/admin/forgot-verify')

    return render_template('admin/forgot.html')


# =====================================================
# VERIFY FORGOT PASSWORD OTP
# =====================================================

@app.route('/admin/forgot-verify', methods=['GET', 'POST'])
def forgot_verify():

    if 'reset_email' not in session:
        return redirect('/admin/forgot')

    if request.method == 'POST':

        user_otp = request.form['otp']

        # Check OTP expiration
        otp_time = session.get('reset_otp_time', 0)

        if time.time() - otp_time > 300:
            session.pop('reset_otp', None)
            session.pop('reset_otp_time', None)

            flash("OTP expired! Please request a new OTP.", "danger")
            return redirect('/admin/forgot')

        # Verify OTP
        if user_otp == session.get('reset_otp'):

            session['reset_verified'] = True

            session.pop('reset_otp', None)
            session.pop('reset_otp_time', None)

            return redirect('/admin/forgot-reset')

        flash("Invalid OTP! Please try again.", "danger")
        return redirect('/admin/forgot-verify')

    return render_template('admin/forgot_otp.html')


# =====================================================
# RESET ADMIN PASSWORD
# =====================================================

@app.route('/admin/forgot-reset', methods=['GET', 'POST'])
def forgot_reset():

    if not session.get('reset_verified') or not session.get('reset_email'):
        return redirect('/admin/forgot')

    if request.method == 'POST':

        password = request.form['password']
        confirm_password = request.form['confirm_password']

        if len(password) < 8:
            flash("Password must contain at least 8 characters!", "danger")
            return redirect('/admin/forgot-reset')

        if password != confirm_password:
            flash("Passwords do not match!", "danger")
            return redirect('/admin/forgot-reset')

        # Hash password using bcrypt
        hashed_password = bcrypt.hashpw(
            password.encode('utf-8'),
            bcrypt.gensalt()
        ).decode('utf-8')

        email = session['reset_email']

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            "UPDATE admin SET password=%s WHERE email=%s",
            (hashed_password, email)
        )

        conn.commit()

        cursor.close()
        conn.close()

        # Clear reset session
        session.pop('reset_email', None)
        session.pop('reset_verified', None)

        flash("Password changed successfully! Please login.", "success")

        return redirect('/admin-login')

    return render_template('admin/forgot_reset.html')



#user routes

# =====================================================
# USER REGISTRATION - SEND OTP
# =====================================================

@app.route('/user-register', methods=['GET', 'POST'])
def user_register():

    if request.method == 'GET':
        return render_template('user/user_register.html')

    name = request.form['name'].strip()
    email = request.form['email'].strip().lower()
    phone = request.form['phone'].strip()
    password = request.form['password']
    confirm_password = request.form['confirm_password']

    if password != confirm_password:
        flash("Passwords do not match!", "danger")
        return redirect('/user-register')

    if len(password) < 8:
        flash("Password must contain at least 8 characters!", "danger")
        return redirect('/user-register')

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute(
        "SELECT user_id FROM users WHERE email=%s",
        (email,)
    )

    existing_user = cursor.fetchone()

    cursor.close()
    conn.close()

    if existing_user:
        flash("Email already registered. Please login!", "danger")
        return redirect('/user-login')

    # Process profile image if uploaded
    profile_image_file = request.files.get('profile_image')
    saved_profile_image = None
    if profile_image_file and profile_image_file.filename:
        from werkzeug.utils import secure_filename
        allowed_extensions = {'png', 'jpg', 'jpeg', 'webp'}
        filename = secure_filename(profile_image_file.filename)
        extension = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
        if extension in allowed_extensions:
            os.makedirs(app.config['USER_UPLOAD_FOLDER'], exist_ok=True)
            saved_profile_image = f"reg_{int(time.time())}_{random.randint(100, 999)}_{filename}"
            save_path = os.path.join(app.config['USER_UPLOAD_FOLDER'], saved_profile_image)
            profile_image_file.save(save_path)

    # Generate OTP
    otp = str(random.randint(100000, 999999))

    # Hash password
    password_hash = bcrypt.hashpw(
        password.encode('utf-8'),
        bcrypt.gensalt()
    ).decode('utf-8')

    # Hash OTP
    otp_hash = bcrypt.hashpw(
        otp.encode('utf-8'),
        bcrypt.gensalt()
    ).decode('utf-8')

    # Store temporary registration in Flask session
    session['user_signup'] = {
        'name': name,
        'email': email,
        'phone': phone,
        'password': password_hash,
        'otp_hash': otp_hash,
        'profile_image': saved_profile_image,
        'expires_at': time.time() + 300
    }

    # Send OTP email
    message = Message(
        subject="SmartCart User Registration OTP",
        sender=config.MAIL_USERNAME,
        recipients=[email]
    )

    message.body = f"""
Hello {name},

Your SmartCart registration OTP is: {otp}

This OTP is valid for 5 minutes.

Do not share this OTP with anyone.
"""

    mail.send(message)

    flash("OTP sent successfully to your email!", "success")
    return redirect('/user-verify-otp')


# =====================================================
# USER OTP VERIFICATION
# =====================================================

@app.route('/user-verify-otp', methods=['GET', 'POST'])
def user_verify_otp():

    pending_user = session.get('user_signup')

    if not pending_user:
        flash("Please register first!", "danger")
        return redirect('/user-register')

    if request.method == 'GET':
        return render_template('user/user_verify_otp.html')

    user_otp = request.form['otp'].strip()

    # Check OTP expiry
    if time.time() > pending_user['expires_at']:

        session.pop('user_signup', None)

        flash("OTP expired! Please register again.", "danger")
        return redirect('/user-register')

    # Verify OTP
    if not bcrypt.checkpw(
        user_otp.encode('utf-8'),
        pending_user['otp_hash'].encode('utf-8')
    ):

        flash("Invalid OTP! Please try again.", "danger")
        return redirect('/user-verify-otp')

    # Save verified customer directly into users table
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO users (name, email, phone, password, profile_image)
        VALUES (%s, %s, %s, %s, %s)
    """, (
        pending_user['name'],
        pending_user['email'],
        pending_user['phone'],
        pending_user['password'],
        pending_user.get('profile_image')
    ))

    conn.commit()

    cursor.close()
    conn.close()

    # Clear temporary session data
    session.pop('user_signup', None)

    flash("Registration successful! Please login.", "success")
    return redirect('/user-login')


# =====================================================
# USER LOGIN
# =====================================================

@app.route('/user-login', methods=['GET', 'POST'])
def user_login():

    if request.method == 'GET':
        return render_template('user/user_login.html')

    email = request.form['email'].strip().lower()
    password = request.form['password']

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute(
        "SELECT * FROM users WHERE email=%s",
        (email,)
    )

    user = cursor.fetchone()

    cursor.close()
    conn.close()

    if not user:
        flash("Email not registered! Please sign up.", "danger")
        return redirect('/user-login')

    stored_password = user['password'].encode('utf-8')

    if not bcrypt.checkpw(
        password.encode('utf-8'),
        stored_password
    ):
        flash("Incorrect password! Try again.", "danger")
        return redirect('/user-login')

    # Create customer session
    session['user_id'] = user['user_id']
    session['user_name'] = user['name']
    session['user_email'] = user['email']
    session['user_profile_image'] = user.get('profile_image')

    flash("Login successful! Welcome to SmartCart.", "success")
    return redirect('/user-dashboard')


# =====================================================
# USER FORGOT PASSWORD - SEND OTP
# =====================================================

@app.route('/user-forgot-password', methods=['GET', 'POST'])
def user_forgot_password():

    if request.method == 'GET':
        return render_template('user/user_forgot_password.html')

    email = request.form['email'].strip().lower()

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute(
        "SELECT user_id, name, email FROM users WHERE email=%s",
        (email,)
    )

    user = cursor.fetchone()
    cursor.close()
    conn.close()

    if not user:
        flash("Email address is not registered!", "danger")
        return redirect('/user-forgot-password')

    otp = str(random.randint(100000, 999999))

    otp_hash = bcrypt.hashpw(
        otp.encode('utf-8'),
        bcrypt.gensalt()
    ).decode('utf-8')

    session['user_password_reset'] = {
        'email': email,
        'otp_hash': otp_hash,
        'expires_at': time.time() + 300,
        'verified': False
    }

    message = Message(
        subject="SmartCart Password Reset OTP",
        sender=config.MAIL_USERNAME,
        recipients=[email]
    )

    message.body = f"""
Hello {user['name']},

Your SmartCart password reset OTP is: {otp}

This OTP is valid for 5 minutes.
Do not share this OTP with anyone.
"""

    mail.send(message)

    flash("OTP sent to your registered email!", "success")
    return redirect('/user-verify-reset-otp')


# =====================================================
# STEP 2: VERIFY OTP
# =====================================================

@app.route('/user-verify-reset-otp', methods=['GET', 'POST'])
def user_verify_reset_otp():

    reset_data = session.get('user_password_reset')

    if not reset_data:
        flash("Please request a password reset first!", "danger")
        return redirect('/user-forgot-password')

    if request.method == 'GET':
        return render_template('user/user_verify_reset_otp.html')

    otp = request.form['otp'].strip()

    if time.time() > reset_data['expires_at']:
        session.pop('user_password_reset', None)
        flash("OTP expired! Please request a new OTP.", "danger")
        return redirect('/user-forgot-password')

    if not otp.isdigit() or len(otp) != 6:
        flash("Enter a valid 6-digit OTP!", "danger")
        return redirect('/user-verify-reset-otp')

    if not bcrypt.checkpw(
        otp.encode('utf-8'),
        reset_data['otp_hash'].encode('utf-8')
    ):
        flash("Invalid OTP! Please try again.", "danger")
        return redirect('/user-verify-reset-otp')

    reset_data['verified'] = True
    session['user_password_reset'] = reset_data

    flash("OTP verified successfully! Set your new password.", "success")
    return redirect('/user-set-new-password')


# =====================================================
# STEP 3: SET NEW PASSWORD
# =====================================================

@app.route('/user-set-new-password', methods=['GET', 'POST'])
def user_set_new_password():

    reset_data = session.get('user_password_reset')

    if not reset_data or not reset_data.get('verified'):
        flash("Please verify your OTP first!", "danger")
        return redirect('/user-forgot-password')

    if time.time() > reset_data['expires_at']:
        session.pop('user_password_reset', None)
        flash("OTP expired! Please request a new OTP.", "danger")
        return redirect('/user-forgot-password')

    if request.method == 'GET':
        return render_template('user/user_set_new_password.html')

    new_password = request.form['new_password']
    confirm_password = request.form['confirm_password']

    if len(new_password) < 8:
        flash("Password must contain at least 8 characters!", "danger")
        return redirect('/user-set-new-password')

    if new_password != confirm_password:
        flash("Passwords do not match!", "danger")
        return redirect('/user-set-new-password')

    new_password_hash = bcrypt.hashpw(
        new_password.encode('utf-8'),
        bcrypt.gensalt()
    ).decode('utf-8')

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE users
        SET password = %s
        WHERE email = %s
    """, (
        new_password_hash,
        reset_data['email']
    ))

    conn.commit()
    cursor.close()
    conn.close()

    session.pop('user_password_reset', None)

    flash("Password reset successful! Please login.", "success")
    return redirect('/user-login')



# =====================================================
# USER DASHBOARD
# =====================================================
@app.route('/user-dashboard')
def user_dashboard():

    if 'user_id' not in session:
        flash("Please login first!", "danger")
        return redirect('/user-login')

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT product_id, name, description, category, price, image
        FROM products
        ORDER BY product_id DESC
    """)

    products = cursor.fetchall()

    cursor.close()
    conn.close()

    # Get cart count
    cart = session.get('cart', {})
    cart_count = sum(
        item['quantity'] for item in cart.values()
    )

    return render_template(
        'user/user_dashboard.html',
        user_name=session['user_name'],
        products=products,
        cart_count=cart_count
    )


# =====================================================
# USER LOGOUT
# =====================================================

@app.route('/user-logout')
def user_logout():

    session.pop('user_id', None)
    session.pop('user_name', None)
    session.pop('user_email', None)

    flash("Logged out successfully!", "success")
    return redirect('/user-login')


# =====================================================
# USER - VIEW PRODUCT DETAILS
# =====================================================

@app.route('/user-product/<int:product_id>')
def user_view_product(product_id):
    if 'user_id' not in session:
        flash("Please login to view product details!", "info")
        return redirect('/user-login')

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        # Fetch product with seller name
        cursor.execute("""
            SELECT p.*, a.name AS seller_name 
            FROM products p 
            LEFT JOIN admin a ON p.admin_id = a.admin_id 
            WHERE p.product_id = %s
        """, (product_id,))
        product = cursor.fetchone()

        if not product:
            flash("Product not found!", "warning")
            return redirect('/user-dashboard')

    finally:
        cursor.close()
        conn.close()

    cart = session.get('cart', {})
    cart_count = sum(int(item['quantity']) for item in cart.values()) if cart else 0

    return render_template(
        'user/view_product.html',
        product=product,
        cart_count=cart_count
    )


# =====================================================
# USER PROFILE
# =====================================================

@app.route('/user-profile')
def user_profile():

    if 'user_id' not in session:
        flash("Please login first!", "danger")
        return redirect('/user-login')

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT user_id, name, email, phone, created_at, profile_image
        FROM users
        WHERE user_id = %s
    """, (session['user_id'],))

    user = cursor.fetchone()

    cursor.close()
    conn.close()

    if not user:
        session.clear()
        flash("User account not found. Please login again.", "danger")
        return redirect('/user-login')

    return render_template('user/user_profile.html', user=user)


# =====================================================
# EDIT USER PROFILE
# =====================================================

@app.route('/user-edit-profile', methods=['GET', 'POST'])
def user_edit_profile():

    if 'user_id' not in session:
        flash("Please login first!", "danger")
        return redirect('/user-login')

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    if request.method == 'POST':

        name = request.form['name'].strip()
        phone = request.form['phone'].strip()
        new_image = request.files.get('profile_image')

        if not name or not phone:
            cursor.close()
            conn.close()
            flash("All fields are required!", "danger")
            return redirect('/user-edit-profile')

        if not phone.isdigit() or len(phone) != 10:
            cursor.close()
            conn.close()
            flash("Enter a valid 10-digit phone number!", "danger")
            return redirect('/user-edit-profile')

        # Fetch current user profile_image
        cursor.execute("SELECT profile_image FROM users WHERE user_id = %s", (session['user_id'],))
        current_user = cursor.fetchone()
        old_image = current_user.get('profile_image') if current_user else None
        final_image = old_image

        if new_image and new_image.filename:
            from werkzeug.utils import secure_filename
            allowed_extensions = {'png', 'jpg', 'jpeg', 'webp'}
            filename = secure_filename(new_image.filename)
            extension = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
            if extension not in allowed_extensions:
                cursor.close()
                conn.close()
                flash("Only PNG, JPG, JPEG and WEBP images are allowed!", "danger")
                return redirect('/user-edit-profile')

            os.makedirs(app.config['USER_UPLOAD_FOLDER'], exist_ok=True)
            final_image = f"user_{session['user_id']}_{int(time.time())}_{filename}"
            new_path = os.path.join(app.config['USER_UPLOAD_FOLDER'], final_image)
            new_image.save(new_path)

            if old_image:
                old_path = os.path.join(app.config['USER_UPLOAD_FOLDER'], old_image)
                if os.path.exists(old_path):
                    try:
                        os.remove(old_path)
                    except Exception as e:
                        print("Old avatar deletion error:", e)

        cursor.execute("""
            UPDATE users
            SET name = %s, phone = %s, profile_image = %s
            WHERE user_id = %s
        """, (name, phone, final_image, session['user_id']))

        conn.commit()

        # Update session
        session['user_name'] = name
        if final_image:
            session['user_profile_image'] = final_image

        cursor.close()
        conn.close()

        flash("Profile updated successfully!", "success")
        return redirect('/user-profile')

    cursor.execute("""
        SELECT user_id, name, email, phone, profile_image
        FROM users
        WHERE user_id = %s
    """, (session['user_id'],))

    user = cursor.fetchone()

    cursor.close()
    conn.close()

    if not user:
        flash("User account not found!", "danger")
        return redirect('/user-login')

    return render_template('user/user_edit_profile.html', user=user)


# ==============================
# USER CART SYSTEM - DAY 11
# ==============================


# COMMON CART SUMMARY FUNCTION
def get_cart_summary(cart):

    cart_count = sum(
        int(item['quantity']) for item in cart.values()
    )

    grand_total = sum(
        float(item['price']) * int(item['quantity'])
        for item in cart.values()
    )

    return cart_count, grand_total


# 1. ADD TO CART - AJAX
@app.route('/user/add-to-cart-ajax/<int:product_id>', methods=['POST'])
def add_to_cart_ajax(product_id):

    if 'user_id' not in session:
        return jsonify({
            'success': False,
            'message': 'Please login first!'
        }), 401

    cart = session.get('cart', {})

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute(
            "SELECT * FROM products WHERE product_id = %s",
            (product_id,)
        )

        product = cursor.fetchone()

    finally:
        cursor.close()
        conn.close()

    if not product:
        return jsonify({
            'success': False,
            'message': 'Product not found!'
        }), 404

    # Read quantity from JSON, form, or query param (default: 1)
    qty_to_add = 1
    if request.is_json and request.json and 'quantity' in request.json:
        try:
            qty_to_add = int(request.json['quantity'])
        except (ValueError, TypeError):
            qty_to_add = 1
    elif 'quantity' in request.form:
        try:
            qty_to_add = int(request.form['quantity'])
        except (ValueError, TypeError):
            qty_to_add = 1
    elif 'qty' in request.args:
        try:
            qty_to_add = int(request.args['qty'])
        except (ValueError, TypeError):
            qty_to_add = 1

    if qty_to_add < 1:
        qty_to_add = 1

    pid = str(product_id)

    if pid in cart:
        cart[pid]['quantity'] += qty_to_add
    else:
        cart[pid] = {
            'name': product['name'],
            'price': float(product['price']),
            'image': product['image'],
            'quantity': qty_to_add
        }

    session['cart'] = cart
    session.modified = True

    cart_count, grand_total = get_cart_summary(cart)

    return jsonify({
        'success': True,
        'message': 'Product added successfully!',
        'cart_count': cart_count,
        'grand_total': grand_total
    })


# 2. VIEW CART
@app.route('/user/cart')
def view_cart():

    if 'user_id' not in session:
        return redirect('/user-login')

    cart = session.get('cart', {})

    print("CART DATA:", cart)

    cart_count, grand_total = get_cart_summary(cart)

    return render_template(
        'user/cart.html',
        cart=cart,
        grand_total=grand_total,
        cart_count=cart_count
    )


# 3. INCREASE QUANTITY - AJAX
@app.route('/user/cart/increase/<pid>', methods=['POST'])
def increase_quantity(pid):

    if 'user_id' not in session:
        return jsonify({
            'success': False,
            'message': 'Please login first!'
        }), 401

    cart = session.get('cart', {})

    if pid not in cart:
        return jsonify({
            'success': False,
            'message': 'Product not found in cart!'
        }), 404

    cart[pid]['quantity'] += 1

    session['cart'] = cart
    session.modified = True

    cart_count, grand_total = get_cart_summary(cart)

    item_total = (
        float(cart[pid]['price']) *
        int(cart[pid]['quantity'])
    )

    return jsonify({
        'success': True,
        'quantity': cart[pid]['quantity'],
        'item_total': item_total,
        'grand_total': grand_total,
        'cart_count': cart_count,
        'removed': False
    })


# 4. DECREASE QUANTITY - AJAX
@app.route('/user/cart/decrease/<pid>', methods=['POST'])
def decrease_quantity(pid):

    if 'user_id' not in session:
        return jsonify({
            'success': False,
            'message': 'Please login first!'
        }), 401

    cart = session.get('cart', {})

    if pid not in cart:
        return jsonify({
            'success': False,
            'message': 'Product not found in cart!'
        }), 404

    if cart[pid]['quantity'] > 1:

        cart[pid]['quantity'] -= 1
        removed = False

        quantity = cart[pid]['quantity']

        item_total = (
            float(cart[pid]['price']) * quantity
        )

    else:

        del cart[pid]

        removed = True
        quantity = 0
        item_total = 0

    session['cart'] = cart
    session.modified = True

    cart_count, grand_total = get_cart_summary(cart)

    return jsonify({
        'success': True,
        'quantity': quantity,
        'item_total': item_total,
        'grand_total': grand_total,
        'cart_count': cart_count,
        'removed': removed
    })


# 5. REMOVE PRODUCT - AJAX
@app.route('/user/cart/remove/<pid>', methods=['POST'])
def remove_from_cart(pid):

    if 'user_id' not in session:
        return jsonify({
            'success': False,
            'message': 'Please login first!'
        }), 401

    cart = session.get('cart', {})

    if pid not in cart:
        return jsonify({
            'success': False,
            'message': 'Product not found in cart!'
        }), 404

    del cart[pid]

    session['cart'] = cart
    session.modified = True

    cart_count, grand_total = get_cart_summary(cart)

    return jsonify({
        'success': True,
        'removed': True,
        'cart_count': cart_count,
        'grand_total': grand_total
    })

# ======================================
# CHECKOUT WITH SESSION ADDRESS
# ======================================

# 1. SELECTED PRODUCTS
@app.route('/user/checkout-selected', methods=['POST'])
def checkout_selected():

    if 'user_id' not in session:
        return redirect('/user-login')

    selected_ids = request.form.getlist('selected_ids')
    cart = session.get('cart', {})

    selected_items = {
        pid: cart[pid]
        for pid in selected_ids
        if pid in cart
    }

    if not selected_items:
        return redirect(url_for('view_cart'))

    # Remember selected products in session
    session['checkout_selected_ids'] = list(selected_items.keys())

    return redirect(url_for('checkout_page'))


# 2. DISPLAY CHECKOUT PAGE
@app.route('/user/checkout')
def checkout_page():

    if 'user_id' not in session:
        return redirect('/user-login')

    cart = session.get('cart', {})
    selected_ids = session.get('checkout_selected_ids', [])

    selected_items = {
        pid: cart[pid]
        for pid in selected_ids
        if pid in cart
    }

    if not selected_items:
        return redirect(url_for('view_cart'))

    selected_total = sum(
        float(item['price']) * int(item['quantity'])
        for item in selected_items.values()
    )

    selected_count = sum(
        int(item['quantity'])
        for item in selected_items.values()
    )

    # Retrieve saved address from session
    saved_address = session.get('delivery_address', {})

    return render_template(
        'user/checkout.html',
        selected_items=selected_items,
        selected_total=selected_total,
        selected_count=selected_count,
        saved_address=saved_address
    )


# 3. SAVE DELIVERY ADDRESS IN SESSION

@app.route('/user/save-delivery-address', methods=['POST'])
def save_delivery_address():

    if 'user_id' not in session:
        return redirect('/user-login')

    full_name = request.form.get('full_name', '').strip()
    phone = request.form.get('phone', '').strip()
    address_line = request.form.get('address_line', '').strip()
    city = request.form.get('city', '').strip()
    state = request.form.get('state', '').strip()
    pincode = request.form.get('pincode', '').strip()
    landmark = request.form.get('landmark', '').strip()

    if not all([
        full_name, phone, address_line,
        city, state, pincode
    ]):
        flash('Please fill all required address fields.', 'error')
        return redirect(url_for('checkout_page'))

    if not phone.isdigit() or len(phone) != 10:
        flash('Enter a valid 10-digit mobile number.', 'error')
        return redirect(url_for('checkout_page'))

    if not pincode.isdigit() or len(pincode) != 6:
        flash('Enter a valid 6-digit pincode.', 'error')
        return redirect(url_for('checkout_page'))

    # Save address in session
    session['delivery_address'] = {
        'full_name': full_name,
        'phone': phone,
        'address_line': address_line,
        'city': city,
        'state': state,
        'pincode': pincode,
        'landmark': landmark
    }

    session.modified = True

    flash('Delivery address saved successfully!', 'success')

    # Redirect to payment page
    return redirect(url_for('user_pay'))


#payment

razorpay_client = razorpay.Client(
    auth=(
        config.RAZORPAY_KEY_ID,
        config.RAZORPAY_KEY_SECRET
    )
)

@app.route('/user/pay')
def user_pay():

    if 'user_id' not in session:
        return redirect('/user-login')

    delivery_address = session.get('delivery_address')

    if not delivery_address:
        flash("Please save your delivery address.", "error")
        return redirect(url_for('checkout_page'))

    cart = session.get('cart', {})
    selected_ids = session.get('checkout_selected_ids', [])

    selected_items = {
        str(pid): cart[str(pid)]
        for pid in selected_ids
        if str(pid) in cart
    }

    if not selected_items:
        flash("No products selected.", "error")
        return redirect(url_for('view_cart'))

    total_amount = sum(
        Decimal(str(item['price'])) * int(item['quantity'])
        for item in selected_items.values()
    )

    razorpay_amount = int(total_amount * 100)

    try:
        razorpay_order = razorpay_client.order.create({
            "amount": razorpay_amount,
            "currency": "INR",
            "payment_capture": 1
        })

        session['razorpay_order_id'] = razorpay_order['id']
        session['razorpay_amount'] = razorpay_amount

    except Exception as e:
        print("Razorpay order creation error:", e)
        flash("Unable to create payment. Please try again.", "error")
        return redirect(url_for('checkout_page'))

    return render_template(
        'user/payment.html',
        selected_items=selected_items,
        total_amount=total_amount,
        delivery_address=delivery_address,
        razorpay_key_id=config.RAZORPAY_KEY_ID,
        razorpay_order_id=razorpay_order['id'],
        razorpay_amount=razorpay_amount
    )



@app.route('/payment-success', methods=['POST'])
def payment_success():

    if 'user_id' not in session:
        return redirect('/user-login')

    payment_id = request.form.get('razorpay_payment_id')
    razorpay_order_id = request.form.get('razorpay_order_id')
    signature = request.form.get('razorpay_signature')

    if not payment_id or not razorpay_order_id or not signature:
        flash("Payment details are missing.", "error")
        return redirect(url_for('view_cart'))

    # Check that this Razorpay order belongs to this session
    if razorpay_order_id != session.get('razorpay_order_id'):
        flash("Invalid payment order.", "error")
        return redirect(url_for('view_cart'))

    conn = None
    cursor = None

    try:
        # Verify Razorpay signature
        razorpay_client.utility.verify_payment_signature({
            'razorpay_order_id': razorpay_order_id,
            'razorpay_payment_id': payment_id,
            'razorpay_signature': signature
        })

        # Fetch payment details directly from Razorpay
        payment = razorpay_client.payment.fetch(payment_id)

        if payment.get('status') != 'captured':
            flash("Payment has not been captured.", "error")
            return redirect(url_for('view_cart'))

        if payment.get('order_id') != razorpay_order_id:
            flash("Payment order does not match.", "error")
            return redirect(url_for('view_cart'))

        expected_amount = session.get('razorpay_amount')

        if expected_amount is None or int(payment.get('amount', 0)) != int(expected_amount):
            flash("Payment amount does not match.", "error")
            return redirect(url_for('view_cart'))

        if payment.get('currency') != 'INR':
            flash("Invalid payment currency.", "error")
            return redirect(url_for('view_cart'))

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # Prevent duplicate order insertion if callback is repeated
        cursor.execute(
            """
            SELECT order_id
            FROM orders
            WHERE razorpay_order_id = %s
            """,
            (razorpay_order_id,)
        )

        existing_order = cursor.fetchone()

        if existing_order:
            order_db_id = existing_order['order_id']

        else:

            delivery_address = session.get('delivery_address', {})
            cart = session.get('cart', {})
            selected_ids = session.get('checkout_selected_ids', [])

            selected_items = {
                str(pid): cart[str(pid)]
                for pid in selected_ids
                if str(pid) in cart
            }

            if not selected_items:
                flash("Selected products are no longer available in your cart.", "error")
                return redirect(url_for('view_cart'))

            # Recalculate the total on the server
            total_amount = sum(
                Decimal(str(item['price'])) * int(item['quantity'])
                for item in selected_items.values()
            )

            if int(total_amount * 100) != int(expected_amount):
                flash("Order amount validation failed.", "error")
                return redirect(url_for('view_cart'))

            # Insert main order
            cursor.execute(
                """
                INSERT INTO orders (
                    user_id,
                    razorpay_order_id,
                    razorpay_payment_id,
                    razorpay_signature,
                    total_amount,
                    currency,
                    payment_status,
                    order_status,
                    full_name,
                    phone,
                    address_line,
                    city,
                    state,
                    pincode,
                    landmark
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    session['user_id'],
                    razorpay_order_id,
                    payment_id,
                    signature,
                    total_amount,
                    'INR',
                    'PAID',
                    'PLACED',
                    delivery_address.get('full_name', ''),
                    delivery_address.get('phone', ''),
                    delivery_address.get('address_line', ''),
                    delivery_address.get('city', ''),
                    delivery_address.get('state', ''),
                    delivery_address.get('pincode', ''),
                    delivery_address.get('landmark', '')
                )
            )

            order_db_id = cursor.lastrowid

            # Insert individual purchased products
            for product_id, item in selected_items.items():

                price = Decimal(str(item['price']))
                quantity = int(item['quantity'])
                subtotal = price * quantity

                cursor.execute(
                    """
                    INSERT INTO order_items (
                        order_id,
                        product_id,
                        product_name,
                        product_image,
                        price,
                        quantity,
                        subtotal
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        order_db_id,
                        int(product_id),
                        item.get('name', 'Product'),
                        item.get('image', ''),
                        price,
                        quantity,
                        subtotal
                    )
                )

            # Save order and items together
            conn.commit()

            # Remove only purchased products from cart
            cart = session.get('cart', {})

            for product_id, item in selected_items.items():

                cart_quantity = int(cart[product_id]['quantity'])
                purchased_quantity = int(item['quantity'])

                if cart_quantity > purchased_quantity:
                    cart[product_id]['quantity'] = (
                        cart_quantity - purchased_quantity
                    )
                else:
                    cart.pop(product_id, None)

            session['cart'] = cart
            session.pop('checkout_selected_ids', None)
            session.pop('razorpay_order_id', None)
            session.pop('razorpay_amount', None)
            session.modified = True

        return render_template(
            'user/payment_success.html',
            payment_id=payment_id,
            order_id=razorpay_order_id,
            database_order_id=order_db_id
        )

    except Exception as e:

        if conn:
            conn.rollback()

        print("Payment verification / database error:", e)

        flash(
            "Payment could not be recorded. Please contact support with your payment ID: "
            + str(payment_id),
            "error"
        )

        return redirect(url_for('view_cart'))

    finally:

        if cursor:
            cursor.close()

        if conn:
            conn.close()


# ==========================================
# MY ORDERS PAGE
# ==========================================

@app.route('/user/my-orders')
def my_orders():

    if 'user_id' not in session:
        flash("Please login to view your orders.", "error")
        return redirect(url_for('user_login'))

    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # Get all orders belonging to the logged-in user
        cursor.execute("""
            SELECT *
            FROM orders
            WHERE user_id = %s
            ORDER BY created_at DESC
        """, (session['user_id'],))

        orders = cursor.fetchall()

        # Get products for each order
        for order in orders:

            cursor.execute("""
                SELECT *
                FROM order_items
                WHERE order_id = %s
            """, (order['order_id'],))

            order['items'] = cursor.fetchall()

        return render_template(
            'user/my_orders.html',
            orders=orders
        )

    except Exception as e:

        print("\n========== MY ORDERS ERROR ==========")
        traceback.print_exc()
        print("=====================================\n")

        flash("Unable to load your orders.", "error")
        return redirect(url_for('user_dashboard'))

    finally:
        if cursor:
            cursor.close()

        if conn:
            conn.close()






# ============================================================
# INVOICE PDF DOWNLOAD — SECURE, CUSTOMER-AUTHENTICATED ROUTE
# ============================================================

@app.route('/user/invoice/<int:order_id>')
def download_invoice(order_id):
    """Generate and return a professional PDF invoice."""

    # 1. Require customer login
    if 'user_id' not in session:
        flash("Please login to download your invoice.", "error")
        return redirect(url_for('user_login'))

    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # 2. Fetch order — verify it belongs to the logged-in user
        cursor.execute("""
            SELECT * FROM orders
            WHERE order_id = %s AND user_id = %s
        """, (order_id, session['user_id']))

        order = cursor.fetchone()

        if not order:
            flash("Invoice not found or access denied.", "error")
            return redirect(url_for('my_orders'))

        # 3. Require successful payment
        if order.get('payment_status') != 'PAID':
            flash("Invoice is only available for paid orders.", "warning")
            return redirect(url_for('my_orders'))

        # 4. Fetch order items
        cursor.execute("""
            SELECT * FROM order_items WHERE order_id = %s
        """, (order_id,))
        items = cursor.fetchall()

        # 5. Fetch customer info
        cursor.execute("""
            SELECT name, email, phone FROM users WHERE user_id = %s
        """, (session['user_id'],))
        customer = cursor.fetchone()

        cursor.close()
        conn.close()

        # 6. Generate PDF
        pdf_bytes = _generate_invoice_pdf(order, items, customer)

        # 7. Return as downloadable attachment
        response = make_response(pdf_bytes)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = (
            f'attachment; filename=ZingZing_Invoice_{order_id}.pdf'
        )
        return response

    except Exception as e:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
        print("Invoice generation error:", e)
        traceback.print_exc()
        flash("Unable to generate invoice. Please try again.", "error")
        return redirect(url_for('my_orders'))


def _generate_invoice_pdf(order, items, customer):
    """Generate a simple, clear, uncluttered e-commerce invoice PDF."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=18*mm, leftMargin=18*mm,
        topMargin=18*mm,   bottomMargin=18*mm
    )

    # Clean, minimal color palette
    TEXT_MAIN   = colors.HexColor('#0F172A') # Dark slate
    TEXT_MUTED  = colors.HexColor('#64748B') # Muted slate
    BLUE_BRAND  = colors.HexColor('#2563EB') # Brand royal blue
    GREEN_PAID  = colors.HexColor('#059669') # Clean emerald
    BORDER_CLR  = colors.HexColor('#E2E8F0') # Subtle border
    TABLE_HDR   = colors.HexColor('#F8FAFC') # Soft gray table header
    TOTAL_BG    = colors.HexColor('#EFF6FF') # Soft blue tint

    def ps(name, **kwargs):
        return ParagraphStyle(name, **kwargs)

    s_brand      = ps('brand',     fontSize=20, fontName='Helvetica-Bold', textColor=BLUE_BRAND, leading=22)
    s_sub        = ps('sub',       fontSize=8.5, fontName='Helvetica',      textColor=TEXT_MUTED, leading=11)
    s_inv_title  = ps('inv_t',     fontSize=16, fontName='Helvetica-Bold', textColor=TEXT_MAIN, alignment=TA_RIGHT, leading=18)
    s_inv_meta   = ps('inv_m',     fontSize=8.5, fontName='Helvetica',      textColor=TEXT_MUTED, alignment=TA_RIGHT, leading=12)
    
    s_sec_title  = ps('sec_t',     fontSize=8.5, fontName='Helvetica-Bold', textColor=TEXT_MUTED, textTransform='uppercase', leading=10)
    s_cust_name  = ps('cust_n',    fontSize=10, fontName='Helvetica-Bold', textColor=TEXT_MAIN, leading=13)
    s_cust_body  = ps('cust_b',    fontSize=8.5, fontName='Helvetica',      textColor=TEXT_MAIN, leading=12)
    s_cust_muted = ps('cust_m',    fontSize=8.5, fontName='Helvetica',      textColor=TEXT_MUTED, leading=12)

    th_l = ps('th_l', fontSize=8.5, fontName='Helvetica-Bold', textColor=TEXT_MAIN, leading=10)
    th_c = ps('th_c', fontSize=8.5, fontName='Helvetica-Bold', textColor=TEXT_MAIN, alignment=TA_CENTER, leading=10)
    th_r = ps('th_r', fontSize=8.5, fontName='Helvetica-Bold', textColor=TEXT_MAIN, alignment=TA_RIGHT, leading=10)

    td_l = ps('td_l', fontSize=9,   fontName='Helvetica',      textColor=TEXT_MAIN, leading=12)
    td_c = ps('td_c', fontSize=9,   fontName='Helvetica',      textColor=TEXT_MUTED, alignment=TA_CENTER, leading=12)
    td_r = ps('td_r', fontSize=9,   fontName='Helvetica',      textColor=TEXT_MAIN, alignment=TA_RIGHT, leading=12)

    tot_l = ps('tot_l', fontSize=9, fontName='Helvetica', textColor=TEXT_MUTED, leading=12)
    tot_r = ps('tot_r', fontSize=9, fontName='Helvetica-Bold', textColor=TEXT_MAIN, alignment=TA_RIGHT, leading=12)
    gtot_l = ps('gtot_l', fontSize=10, fontName='Helvetica-Bold', textColor=BLUE_BRAND, leading=13)
    gtot_r = ps('gtot_r', fontSize=10.5, fontName='Helvetica-Bold', textColor=BLUE_BRAND, alignment=TA_RIGHT, leading=13)

    s_foot = ps('foot', fontSize=8, fontName='Helvetica', textColor=TEXT_MUTED, alignment=TA_CENTER, leading=11)

    story = []

    # 1. Header: Brand on left, Invoice metadata on right
    inv_num = f"INV-{order.get('order_id', 0):06d}"
    created = order.get('created_at')
    if hasattr(created, 'strftime'):
        inv_date = created.strftime('%d %b %Y')
    else:
        inv_date = str(created or datetime.now().strftime('%d %b %Y'))

    hdr_left = [
        Paragraph('<b>Zing-Zing</b>', s_brand),
        Spacer(1, 1*mm),
        Paragraph('Online Shopping Store', s_sub),
        Paragraph('support@zingzing.in | www.zingzing.com', s_sub),
    ]

    hdr_right = [
        Paragraph('INVOICE', s_inv_title),
        Spacer(1, 1.5*mm),
        Paragraph(f'<b>Invoice No:</b> {inv_num}', s_inv_meta),
        Paragraph(f'<b>Order Date:</b> {inv_date}', s_inv_meta),
        Paragraph(f"<b>Order ID:</b> #{order.get('order_id', 0)}", s_inv_meta),
    ]

    hdr_tbl = Table([[hdr_left, hdr_right]], colWidths=[94*mm, 80*mm])
    hdr_tbl.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING', (0,0), (-1,-1), 0),
        ('RIGHTPADDING', (0,0), (-1,-1), 0),
        ('TOPPADDING', (0,0), (-1,-1), 0),
        ('BOTTOMPADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(hdr_tbl)
    story.append(Spacer(1, 3*mm))
    story.append(HRFlowable(width='100%', thickness=1, color=BORDER_CLR, spaceAfter=5*mm))

    # 2. Customer & Delivery Info + Order Status (Clean side-by-side)
    cust_name  = customer.get('name', 'Customer') if customer else 'Customer'
    cust_email = customer.get('email', '') if customer else ''
    cust_phone = customer.get('phone', '') if customer else ''

    addr = order.get('address_line', '')
    city = order.get('city', '')
    state = order.get('state', '')
    pincode = order.get('pincode', '')
    loc_parts = [p for p in [city, state] if p]
    loc = ', '.join(loc_parts)
    if pincode:
        loc = f'{loc} - {pincode}' if loc else f'PIN: {pincode}'

    col_customer = [
        Paragraph('BILLED &amp; DELIVERED TO', s_sec_title),
        Spacer(1, 1.5*mm),
        Paragraph(cust_name, s_cust_name),
    ]
    if addr:
        col_customer.append(Paragraph(addr, s_cust_body))
    if loc:
        col_customer.append(Paragraph(loc, s_cust_body))
    if cust_phone:
        col_customer.append(Paragraph(f'Phone: {cust_phone}', s_cust_muted))
    if cust_email:
        col_customer.append(Paragraph(f'Email: {cust_email}', s_cust_muted))

    pay_status = str(order.get('payment_status', 'PAID')).upper()
    pay_display = 'PAID' if pay_status in ['PAID', 'COMPLETED', 'SUCCESS'] else pay_status

    col_order = [
        Paragraph('PAYMENT &amp; ORDER DETAILS', s_sec_title),
        Spacer(1, 1.5*mm),
        Paragraph(f'<b>Payment Status:</b> <font color="#059669"><b>{pay_display}</b></font>', s_cust_body),
        Paragraph('<b>Payment Method:</b> Prepaid / Online', s_cust_body),
    ]
    if order.get('razorpay_payment_id'):
        col_order.append(Paragraph(f"<b>Payment ID:</b> {order['razorpay_payment_id']}", s_cust_muted))
    if order.get('razorpay_order_id'):
        col_order.append(Paragraph(f"<b>Razorpay Order:</b> {order['razorpay_order_id']}", s_cust_muted))

    info_tbl = Table([[col_customer, col_order]], colWidths=[94*mm, 80*mm])
    info_tbl.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING', (0,0), (-1,-1), 0),
        ('RIGHTPADDING', (0,0), (-1,-1), 0),
        ('TOPPADDING', (0,0), (-1,-1), 0),
        ('BOTTOMPADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(info_tbl)
    story.append(Spacer(1, 6*mm))

    # 3. Simple Items Table (Clean, no clutter, no HSN codes)
    # Total width = 174mm
    # # (10mm), Item Description (94mm), Price (26mm), Qty (16mm), Total (28mm)
    col_w = [10*mm, 94*mm, 26*mm, 16*mm, 28*mm]
    table_data = [[
        Paragraph('#', th_c),
        Paragraph('ITEM DESCRIPTION', th_l),
        Paragraph('PRICE', th_r),
        Paragraph('QTY', th_c),
        Paragraph('TOTAL', th_r),
    ]]

    subtotal = Decimal('0')
    for idx, itm in enumerate(items, 1):
        p_name = itm.get('product_name', 'Product')
        price = Decimal(str(itm.get('price', 0)))
        qty = int(itm.get('quantity', 0))
        item_sub = Decimal(str(itm.get('subtotal', price * qty)))
        subtotal += item_sub

        table_data.append([
            Paragraph(str(idx), td_c),
            Paragraph(f'<b>{p_name}</b>', td_l),
            Paragraph(f'Rs. {price:,.2f}', td_r),
            Paragraph(str(qty), td_c),
            Paragraph(f'Rs. {item_sub:,.2f}', td_r),
        ])

    items_table = Table(table_data, colWidths=col_w, repeatRows=1)
    items_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), TABLE_HDR),
        ('TOPPADDING', (0,0), (-1,0), 6),
        ('BOTTOMPADDING', (0,0), (-1,0), 6),
        ('LINEBELOW', (0,0), (-1,0), 1, BORDER_CLR),
        ('LINEABOVE', (0,0), (-1,0), 1, BORDER_CLR),
        ('TOPPADDING', (0,1), (-1,-1), 6),
        ('BOTTOMPADDING', (0,1), (-1,-1), 6),
        ('LEFTPADDING', (0,0), (-1,-1), 5),
        ('RIGHTPADDING', (0,0), (-1,-1), 5),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('LINEBELOW', (0,1), (-1,-1), 0.5, BORDER_CLR),
    ]))
    story.append(items_table)
    story.append(Spacer(1, 4*mm))

    # 4. Summary Totals (Clean, simple, right-aligned)
    grand_total = Decimal(str(order.get('total_amount', subtotal)))

    totals_data = [
        [Paragraph('Subtotal', tot_l), Paragraph(f'Rs. {subtotal:,.2f}', tot_r)],
        [Paragraph('Delivery', tot_l), Paragraph('<font color="#059669"><b>FREE</b></font>', tot_r)],
        [Paragraph('Total Paid', gtot_l), Paragraph(f'Rs. {grand_total:,.2f}', gtot_r)],
    ]

    totals_tbl = Table(totals_data, colWidths=[44*mm, 32*mm])
    totals_tbl.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-2), 3),
        ('BOTTOMPADDING', (0,0), (-1,-2), 3),
        ('LEFTPADDING', (0,0), (-1,-1), 6),
        ('RIGHTPADDING', (0,0), (-1,-1), 6),
        ('LINEBELOW', (0,0), (-1,-2), 0.5, BORDER_CLR),
        # Highlight total row
        ('BACKGROUND', (0,-1), (-1,-1), TOTAL_BG),
        ('TOPPADDING', (0,-1), (-1,-1), 5),
        ('BOTTOMPADDING', (0,-1), (-1,-1), 5),
        ('BOX', (0,-1), (-1,-1), 0.5, colors.HexColor('#BFDBFE')),
    ]))

    summary_wrap = Table([['', totals_tbl]], colWidths=[98*mm, 76*mm])
    summary_wrap.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING', (0,0), (-1,-1), 0),
        ('RIGHTPADDING', (0,0), (-1,-1), 0),
        ('TOPPADDING', (0,0), (-1,-1), 0),
        ('BOTTOMPADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(summary_wrap)
    story.append(Spacer(1, 14*mm))

    # 5. Clean, Simple Footer
    story.append(HRFlowable(width='100%', thickness=0.5, color=BORDER_CLR, spaceAfter=4*mm))
    story.append(Paragraph('Thank you for shopping with Zing-Zing!', ps('ft_b', fontSize=8.5, fontName='Helvetica-Bold', textColor=TEXT_MAIN, alignment=TA_CENTER, leading=11)))
    story.append(Spacer(1, 1*mm))
    story.append(Paragraph('For any order questions or support, email us at support@zingzing.in', s_foot))

    doc.build(story)
    return buffer.getvalue()


# RUN APP
if __name__ == '__main__':
    app.run(debug=True)



