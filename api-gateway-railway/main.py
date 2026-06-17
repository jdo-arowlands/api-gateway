from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from typing import Optional, List
import os, shutil, uuid, smtplib, json
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from jose import JWTError, jwt
from passlib.context import CryptContext
from dotenv import load_dotenv
import cloudinary
import cloudinary.uploader

from database import SessionLocal, engine, Base
import models, schemas

load_dotenv()
Base.metadata.create_all(bind=engine)

cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
    secure=True,
)

def upload_image_to_cloudinary(file: UploadFile) -> str:
    result = cloudinary.uploader.upload(
        file.file,
        folder="tubrent",
        resource_type="image",
    )
    return result["secure_url"]

app = FastAPI(title="TubRent API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

SECRET_KEY = os.getenv("SECRET_KEY", "tubrent-super-secret-key-change-in-prod")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
FROM_EMAIL = os.getenv("FROM_EMAIL", SMTP_USER)
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", SMTP_USER)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def hash_password(password: str):
    return pwd_context.hash(password)

def verify_password(plain, hashed):
    return pwd_context.verify(plain, hashed)

def create_token(data: dict, expires_delta=None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=15))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    credentials_exception = HTTPException(status_code=401, detail="Could not validate credentials")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    user = db.query(models.User).filter(models.User.email == email).first()
    if user is None:
        raise credentials_exception
    return user

def get_admin_user(current_user: models.User = Depends(get_current_user)):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user

def send_order_email(to_email: str, customer_name: str, order: models.Order, items: list):
    if not SMTP_USER:
        print(f"[EMAIL SKIPPED - no SMTP config] Order #{order.id} to {to_email}")
        return
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"TubRent Booking Confirmed — Order #{order.id}"
        msg["From"] = FROM_EMAIL
        msg["To"] = to_email

        items_html = ""
        for item in items:
            items_html += f"""
            <tr>
                <td style="padding:8px 12px;border-bottom:1px solid #eee">{item['product_name']}</td>
                <td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:center">{item['quantity']}</td>
                <td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:right">${item['unit_price']:.2f}/day</td>
                <td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:right">${item['subtotal']:.2f}</td>
            </tr>"""

        html = f"""
        <html><body style="font-family:'Helvetica Neue',Arial,sans-serif;background:#f5f5f0;margin:0;padding:0">
        <div style="max-width:600px;margin:40px auto;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 2px 16px rgba(0,0,0,0.08)">
            <div style="background:#1a1a1a;padding:32px 40px">
                <h1 style="color:#f0e68c;margin:0;font-size:28px;letter-spacing:2px">TUBRENT</h1>
                <p style="color:#aaa;margin:4px 0 0">Storage Tub Rental Co.</p>
            </div>
            <div style="padding:40px">
                <h2 style="color:#1a1a1a;margin:0 0 8px">Booking Confirmed!</h2>
                <p style="color:#666">Hi {customer_name}, your rental booking has been received.</p>
                <div style="background:#f9f9f6;border-radius:6px;padding:20px;margin:24px 0">
                    <p style="margin:0 0 4px;color:#999;font-size:12px;text-transform:uppercase;letter-spacing:1px">Order Details</p>
                    <p style="margin:0;font-size:20px;font-weight:700;color:#1a1a1a">Order #{order.id}</p>
                    <p style="margin:4px 0 0;color:#666">Placed: {order.created_at.strftime('%B %d, %Y at %I:%M %p')}</p>
                    <p style="margin:4px 0 0;color:#666">Rental Period: <strong>{order.rental_start_date}</strong> → <strong>{order.rental_end_date}</strong></p>
                </div>
                <table style="width:100%;border-collapse:collapse;margin:16px 0">
                    <thead>
                        <tr style="background:#f5f5f0">
                            <th style="padding:10px 12px;text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:1px;color:#666">Item</th>
                            <th style="padding:10px 12px;text-align:center;font-size:11px;text-transform:uppercase;letter-spacing:1px;color:#666">Qty</th>
                            <th style="padding:10px 12px;text-align:right;font-size:11px;text-transform:uppercase;letter-spacing:1px;color:#666">Rate</th>
                            <th style="padding:10px 12px;text-align:right;font-size:11px;text-transform:uppercase;letter-spacing:1px;color:#666">Subtotal</th>
                        </tr>
                    </thead>
                    <tbody>{items_html}</tbody>
                </table>
                <div style="text-align:right;border-top:2px solid #1a1a1a;padding-top:12px;margin-top:8px">
                    <span style="font-size:18px;font-weight:700">Total: ${order.total_amount:.2f}</span>
                </div>
                {f'<div style="margin-top:24px;padding:16px;background:#fffbea;border-left:4px solid #f0e68c;border-radius:4px"><p style="margin:0;color:#666"><strong>Notes:</strong> {order.notes}</p></div>' if order.notes else ''}
                <p style="margin:32px 0 0;color:#999;font-size:13px">Payment is due at pickup. We will contact you to coordinate delivery/pickup logistics. Questions? Reply to this email.</p>
            </div>
        </div>
        </body></html>"""

        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(FROM_EMAIL, [to_email, ADMIN_EMAIL], msg.as_string())
        print(f"Email sent to {to_email}")
    except Exception as e:
        print(f"Email error: {e}")


# AUTH

@app.post("/api/auth/register", response_model=schemas.UserOut)
def register(user: schemas.UserCreate, db: Session = Depends(get_db)):
    if db.query(models.User).filter(models.User.email == user.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")
    db_user = models.User(
        email=user.email,
        name=user.name,
        phone=user.phone,
        hashed_password=hash_password(user.password),
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user

@app.post("/api/auth/login", response_model=schemas.Token)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    token = create_token({"sub": user.email}, timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    return {"access_token": token, "token_type": "bearer", "user": schemas.UserOut.from_orm(user)}

@app.get("/api/auth/me", response_model=schemas.UserOut)
def me(current_user: models.User = Depends(get_current_user)):
    return current_user


# PRODUCTS

@app.get("/api/products", response_model=List[schemas.ProductOut])
def list_products(db: Session = Depends(get_db)):
    return db.query(models.Product).filter(models.Product.is_active == True).all()

@app.get("/api/products/{product_id}", response_model=schemas.ProductOut)
def get_product(product_id: int, db: Session = Depends(get_db)):
    p = db.query(models.Product).filter(models.Product.id == product_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Product not found")
    return p

@app.post("/api/admin/products", response_model=schemas.ProductOut)
def create_product(
    name: str = Form(...),
    description: str = Form(...),
    price_per_day: float = Form(...),
    quantity_available: int = Form(...),
    size_dimensions: str = Form(""),
    weight_capacity: str = Form(""),
    color: str = Form(""),
    category: str = Form(""),
    is_active: bool = Form(True),
    image: UploadFile = File(None),
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_admin_user),
):
    image_url = None
    if image and image.filename:
        image_url = upload_image_to_cloudinary(image)

    product = models.Product(
        name=name, description=description, price_per_day=price_per_day,
        quantity_available=quantity_available, size_dimensions=size_dimensions,
        weight_capacity=weight_capacity, color=color, category=category,
        is_active=is_active, image_url=image_url,
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    return product

@app.put("/api/admin/products/{product_id}", response_model=schemas.ProductOut)
def update_product(
    product_id: int,
    name: str = Form(...),
    description: str = Form(...),
    price_per_day: float = Form(...),
    quantity_available: int = Form(...),
    size_dimensions: str = Form(""),
    weight_capacity: str = Form(""),
    color: str = Form(""),
    category: str = Form(""),
    is_active: bool = Form(True),
    image: UploadFile = File(None),
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_admin_user),
):
    product = db.query(models.Product).filter(models.Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    if image and image.filename:
        product.image_url = upload_image_to_cloudinary(image)

    product.name = name
    product.description = description
    product.price_per_day = price_per_day
    product.quantity_available = quantity_available
    product.size_dimensions = size_dimensions
    product.weight_capacity = weight_capacity
    product.color = color
    product.category = category
    product.is_active = is_active
    db.commit()
    db.refresh(product)
    return product

@app.delete("/api/admin/products/{product_id}")
def delete_product(product_id: int, db: Session = Depends(get_db), admin: models.User = Depends(get_admin_user)):
    product = db.query(models.Product).filter(models.Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    product.is_active = False
    db.commit()
    return {"ok": True}


# ORDERS

@app.post("/api/orders", response_model=schemas.OrderOut)
def create_order(order_data: schemas.OrderCreate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    total = 0.0
    validated_items = []

    for item in order_data.items:
        product = db.query(models.Product).filter(models.Product.id == item.product_id).first()
        if not product:
            raise HTTPException(status_code=404, detail=f"Product {item.product_id} not found")
        if item.quantity > product.quantity_available:
            raise HTTPException(status_code=400, detail=f"Only {product.quantity_available} units of '{product.name}' available")

        start = datetime.strptime(order_data.rental_start_date, "%Y-%m-%d")
        end = datetime.strptime(order_data.rental_end_date, "%Y-%m-%d")
        days = max(1, (end - start).days)
        subtotal = product.price_per_day * item.quantity * days
        total += subtotal
        validated_items.append({"product": product, "quantity": item.quantity, "days": days, "subtotal": subtotal})

    order = models.Order(
        user_id=current_user.id,
        rental_start_date=order_data.rental_start_date,
        rental_end_date=order_data.rental_end_date,
        notes=order_data.notes,
        total_amount=total,
        status="pending",
    )
    db.add(order)
    db.flush()

    email_items = []
    for vi in validated_items:
        oi = models.OrderItem(
            order_id=order.id,
            product_id=vi["product"].id,
            quantity=vi["quantity"],
            unit_price=vi["product"].price_per_day,
            subtotal=vi["subtotal"],
        )
        db.add(oi)
        email_items.append({
            "product_name": vi["product"].name,
            "quantity": vi["quantity"],
            "unit_price": vi["product"].price_per_day,
            "subtotal": vi["subtotal"],
        })

    db.commit()
    db.refresh(order)

    send_order_email(current_user.email, current_user.name, order, email_items)
    return order

@app.get("/api/orders/my", response_model=List[schemas.OrderOut])
def my_orders(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    return db.query(models.Order).filter(models.Order.user_id == current_user.id).order_by(models.Order.created_at.desc()).all()

@app.get("/api/admin/orders", response_model=List[schemas.OrderAdminOut])
def admin_orders(db: Session = Depends(get_db), admin: models.User = Depends(get_admin_user)):
    return db.query(models.Order).order_by(models.Order.created_at.desc()).all()

@app.put("/api/admin/orders/{order_id}/status")
def update_order_status(order_id: int, payload: schemas.StatusUpdate, db: Session = Depends(get_db), admin: models.User = Depends(get_admin_user)):
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    order.status = payload.status
    db.commit()
    return {"ok": True}

@app.get("/api/admin/users", response_model=List[schemas.UserOut])
def admin_users(db: Session = Depends(get_db), admin: models.User = Depends(get_admin_user)):
    return db.query(models.User).all()


# SEED ADMIN

@app.on_event("startup")
def seed():
    db = SessionLocal()
    admin_email = os.getenv("ADMIN_EMAIL", "admin@tubrent.com")
    if not db.query(models.User).filter(models.User.email == admin_email).first():
        admin = models.User(
            email=admin_email,
            name="Admin",
            hashed_password=hash_password(os.getenv("ADMIN_PASSWORD", "admin123")),
            is_admin=True,
        )
        db.add(admin)
        db.commit()
        print(f"Admin seeded: {admin_email}")
    db.close()
