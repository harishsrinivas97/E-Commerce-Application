# 🛍️ Zing-Zing — Full-Stack E-Commerce Application

![Python](https://img.shields.io/badge/Python-3.12+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.x-000000?style=for-the-badge&logo=flask&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-Database-003B57?style=for-the-badge&logo=sqlite&logoColor=white)
![Bootstrap](https://img.shields.io/badge/Bootstrap-5.3-7952B3?style=for-the-badge&logo=bootstrap&logoColor=white)
![Razorpay](https://img.shields.io/badge/Razorpay-Payment%20Gateway-0C2340?style=for-the-badge&logo=razorpay&logoColor=white)

**Zing-Zing** is a modern, responsive full-stack e-commerce web platform built with **Flask**, **SQLite**, and **Bootstrap 5**. It offers an end-to-end shopping experience for customers and a comprehensive administrative dashboard for store management.

---

## ✨ Features

### 👤 Customer Experience
* **Authentication & Security**: User registration with OTP email verification, secure bcrypt password hashing, and OTP-based password reset.
* **Product Catalog**: Browse products with category filtering, live keyword search, and responsive product cards.
* **Product Details**: High-resolution image viewing, quantity stepper, stock status, seller info, and dynamic "Similar Products" recommendations.
* **Shopping Cart**: Real-time cart management with quantity increment/decrement, item removal, and subtotal calculation.
* **Checkout & Address Book**: Delivery address form with validation and saved shipping profiles.
* **Payment Integration**: Seamless checkout via **Razorpay Payment Gateway** supporting UPI, Cards, and Net Banking.
* **Order History & PDF Invoices**: View past orders and download professionally styled PDF tax invoices generated on-the-fly with **ReportLab**.
* **Profile Management**: Update full name, phone number, and upload profile photos with instant live preview.

### 🛡️ Admin Portal
* **Dashboard Overview**: Key performance metrics (Total Products, Registered Users, Total Orders, Paid Orders, Gross Revenue).
* **Inventory Management**: Complete CRUD operations for products (Add, Edit, View, Delete) with image uploads and category tags.
* **Administrator Profile**: Admin credential management, privilege view, and photo customizer.
* **Adaptive Navigation**: Collapsible mobile sidebar with backdrop blur and touch dismiss.

### 🎨 Theme & UI/UX
* **Light / Dark Theme**: Persistent one-click theme switcher across all pages.
* **Fully Responsive**: Optimized for phones (360px+), tablets, laptops, and ultra-wide desktop monitors.
* **Accessibility**: High-contrast badges, accessible dropdown menus, and intuitive breadcrumbs.

---

## 🗂️ Project Structure

```text
zing-zing/
├── Database/
│   └── smartcart.db              # SQLite database (auto-initialized)
├── static/
│   ├── css/
│   │   └── style.css             # Main styling, dark theme & mobile rules
│   ├── images/                   # Static icons & brand assets
│   └── uploads/                  # Upload directories (auto-created)
│       ├── admin_profiles/
│       ├── product_images/
│       └── user_profiles/
├── templates/
│   ├── admin/                    # Admin portal templates
│   │   ├── admin_base.html
│   │   ├── admin_login.html
│   │   ├── admin_signup.html
│   │   ├── admin_profile.html
│   │   ├── admin_edit_profile.html
│   │   ├── dashboard.html
│   │   ├── add_item.html
│   │   ├── item_list.html
│   │   ├── update_item.html
│   │   └── ...
│   ├── user/                     # Customer portal templates
│   │   ├── user_dashboard.html
│   │   ├── user_login.html
│   │   ├── user_register.html
│   │   ├── user_profile.html
│   │   ├── user_edit_profile.html
│   │   ├── cart.html
│   │   ├── checkout.html
│   │   ├── payment.html
│   │   ├── payment_success.html
│   │   ├── my_orders.html
│   │   ├── view_product.html
│   │   └── ...
│   ├── base.html                 # Customer shared layout & navigation
│   └── index.html                # Public landing page
├── .env.example                  # Environment configuration template
├── .gitignore                    # Git rules for production deployment
├── app.py                        # Core Flask application & routes
├── config.py                     # Configuration loader with python-dotenv
├── README.md                     # Project documentation
└── requirements.txt              # Python dependencies
```

---

## 🚀 Quick Start Guide

### Prerequisites
* **Python 3.10+** installed on your system.
* **Git** installed.

---

### 1. Clone the Repository
```bash
git clone https://github.com/harishsrinivas97/E-Commerce-Application.git
cd E-Commerce-Application
```

---

### 2. Create and Activate Virtual Environment

**Windows (PowerShell):**
```powershell
python -m venv myvenv
.\myvenv\Scripts\Activate.ps1
```

**macOS / Linux:**
```bash
python3 -m venv myvenv
source myvenv/bin/activate
```

---

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

---

### 4. Configure Environment Variables
Copy `.env.example` to create your own `.env` file:

```bash
cp .env.example .env
```

Open `.env` in a text editor and fill in your credentials:

```ini
# Flask Secret Key
SECRET_KEY=your_generated_secret_key_here

# Gmail SMTP Settings (for OTP emails)
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USE_TLS=True
MAIL_USERNAME=your_email@gmail.com
MAIL_PASSWORD=your_gmail_app_password

# Razorpay Test Credentials (from dashboard.razorpay.com)
RAZORPAY_KEY_ID=rzp_test_yourKeyId
RAZORPAY_KEY_SECRET=yourKeySecret
```

> [!TIP]
> For `MAIL_PASSWORD`, generate a 16-character **App Password** from your Google Account:
> **Manage Google Account → Security → 2-Step Verification → App Passwords**.

---

### 5. Run the Application
```bash
python app.py
```

* The SQLite database schema (`Database/smartcart.db`) and upload folders are **automatically created** on the first run.
* Open your browser and navigate to:
  ```text
  http://127.0.0.1:5000
  ```

---

## 🔑 Initial Setup & First Login

1. **Admin Account**:
   * Navigate to `http://127.0.0.1:5000/admin-signup`.
   * Fill in your name, email, and password.
   * Verify using the OTP sent to your email.
   * Log into the Admin Dashboard at `http://127.0.0.1:5000/admin-login` and start adding products!

2. **Customer Account**:
   * Navigate to `http://127.0.0.1:5000/user-register`.
   * Register with your name, email, phone number, and password.
   * Verify your OTP to begin browsing, adding items to your cart, and placing orders.

---

## 🛠️ Technology Stack

| Component | Technology | Description |
| :--- | :--- | :--- |
| **Backend** | Python 3, Flask | Lightweight WSGI web application framework |
| **Database** | SQLite 3 | Serverless, zero-configuration embedded database |
| **Security** | Bcrypt | Salted password hashing algorithm |
| **Mailing** | Flask-Mail (SMTP) | Automated OTP delivery for signups and password resets |
| **Payments** | Razorpay SDK | Secure online transactions |
| **Invoicing** | ReportLab | Programmatic generation of branded PDF receipts |
| **Frontend** | Bootstrap 5, FontAwesome 6 | Responsive styling, mobile navigation, and modern iconography |

---

## 📄 License
This project is open-source and available under the [MIT License](LICENSE).
