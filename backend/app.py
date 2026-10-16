import os, re, uuid, logging, secrets, time, base64, asyncio
import hashlib, smtplib
import ipaddress
import zipfile
from xml.sax.saxutils import escape as xml_escape
from io import BytesIO
from email.message import EmailMessage
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
from typing import Optional
import jwt
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token as google_id_token
from PIL import Image, UnidentifiedImageError
import pyotp
from cryptography.fernet import Fernet, InvalidToken
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import create_engine, String, Text, Float, Integer, Boolean, DateTime, ForeignKey, Index, func, select, text, or_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker, Session, relationship
from pwdlib import PasswordHash

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format='{"time":"%(asctime)s","level":"%(levelname)s","message":"%(message)s"}')
log=logging.getLogger("khetx")
DB=os.getenv("DATABASE_URL","postgresql+psycopg://bazaar:bazaar@localhost:5432/bharat_bazaar")
UPLOAD_CONCURRENCY=max(1,int(os.getenv("IMAGE_UPLOAD_CONCURRENCY","2")))
UPLOAD_SEMAPHORE=asyncio.Semaphore(UPLOAD_CONCURRENCY)
JWT_SECRET=os.getenv("JWT_SECRET","").strip()
if len(JWT_SECRET)<32: raise RuntimeError("JWT_SECRET must be configured with at least 32 characters")
TOTP_CIPHER=Fernet(base64.urlsafe_b64encode(hashlib.sha256(("khetx-admin-totp:"+JWT_SECRET).encode()).digest()))
SESSION_COOKIE="khetx_session"
ADMIN_INACTIVITY_MINUTES=30
engine=create_engine(DB,pool_pre_ping=True)
SessionLocal=sessionmaker(bind=engine,expire_on_commit=False)
class Base(DeclarativeBase): pass
class User(Base):
    __tablename__="users"
    id:Mapped[int]=mapped_column(primary_key=True); email:Mapped[str]=mapped_column(String(254),unique=True,index=True); name:Mapped[str]=mapped_column(String(120)); phone:Mapped[str]=mapped_column(String(24),default=""); password_hash:Mapped[str]=mapped_column(String(255)); role:Mapped[str]=mapped_column(String(16),default="buyer"); account_status:Mapped[str]=mapped_column(String(16),default="active",index=True); account_status_note:Mapped[str]=mapped_column(Text,default=""); totp_secret_ciphertext:Mapped[Optional[str]]=mapped_column(Text,nullable=True); totp_enabled:Mapped[bool]=mapped_column(Boolean,default=False,nullable=False); google_sub:Mapped[Optional[str]]=mapped_column(String(255),nullable=True); photo_url:Mapped[str]=mapped_column(String(500),default=""); referred_by_code:Mapped[str]=mapped_column(String(40),default=""); terms_accepted_at:Mapped[Optional[datetime]]=mapped_column(DateTime(timezone=True),nullable=True); privacy_accepted_at:Mapped[Optional[datetime]]=mapped_column(DateTime(timezone=True),nullable=True); created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))
class BuyerAppeal(Base):
    __tablename__="buyer_appeals"
    id:Mapped[int]=mapped_column(primary_key=True); user_id:Mapped[Optional[int]]=mapped_column(ForeignKey("users.id"),nullable=True,index=True); email:Mapped[str]=mapped_column(String(254),index=True); message:Mapped[str]=mapped_column(Text); status:Mapped[str]=mapped_column(String(16),default="open",index=True); admin_note:Mapped[str]=mapped_column(Text,default=""); reviewed_by:Mapped[Optional[int]]=mapped_column(ForeignKey("users.id"),nullable=True); created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc)); reviewed_at:Mapped[Optional[datetime]]=mapped_column(DateTime(timezone=True),nullable=True)
class Listing(Base):
    __tablename__="listings"
    id:Mapped[int]=mapped_column(primary_key=True); owner_id:Mapped[int]=mapped_column(ForeignKey("users.id"),index=True); seller_name:Mapped[str]=mapped_column(String(120)); business_name:Mapped[str]=mapped_column(String(160)); address:Mapped[str]=mapped_column(String(300)); city:Mapped[str]=mapped_column(String(120),index=True); pincode:Mapped[str]=mapped_column(String(10),default=""); phone:Mapped[str]=mapped_column(String(24)); whatsapp:Mapped[str]=mapped_column(String(24)); name:Mapped[str]=mapped_column(String(180),index=True); category:Mapped[str]=mapped_column(String(100),index=True); description:Mapped[str]=mapped_column(Text,default=""); image:Mapped[str]=mapped_column(String(500),default=""); price:Mapped[Optional[float]]=mapped_column(Float,nullable=True); price_label:Mapped[str]=mapped_column(String(100),default=""); moq:Mapped[int]=mapped_column(Integer); unit:Mapped[str]=mapped_column(String(40)); quantity:Mapped[str]=mapped_column(String(80)); lat:Mapped[Optional[float]]=mapped_column(Float,nullable=True); lon:Mapped[Optional[float]]=mapped_column(Float,nullable=True); status:Mapped[str]=mapped_column(String(24),default="pending",index=True); published:Mapped[bool]=mapped_column(Boolean,default=False,index=True); featured:Mapped[bool]=mapped_column(Boolean,default=False); note:Mapped[str]=mapped_column(Text,default=""); reviewer_id:Mapped[Optional[int]]=mapped_column(ForeignKey("users.id"),nullable=True); reviewed_at:Mapped[Optional[datetime]]=mapped_column(DateTime(timezone=True),nullable=True); created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))
    __table_args__=(Index("ix_public_listings","status","published"),)
class Review(Base):
    __tablename__="reviews"
    id:Mapped[int]=mapped_column(primary_key=True); seller_id:Mapped[int]=mapped_column(ForeignKey("users.id"),index=True); buyer_id:Mapped[int]=mapped_column(ForeignKey("users.id"),index=True); rating:Mapped[int]=mapped_column(Integer); body:Mapped[str]=mapped_column(String(1200)); status:Mapped[str]=mapped_column(String(20),default="visible"); verified:Mapped[bool]=mapped_column(Boolean,default=False); created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))
class Audit(Base):
    __tablename__="audit_log"
    id:Mapped[int]=mapped_column(primary_key=True); actor_id:Mapped[Optional[int]]=mapped_column(ForeignKey("users.id"),nullable=True); action:Mapped[str]=mapped_column(String(80)); entity:Mapped[str]=mapped_column(String(80)); entity_id:Mapped[int]=mapped_column(Integer); note:Mapped[str]=mapped_column(Text,default=""); created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))
class SchemaMigration(Base):
    __tablename__="schema_migrations"
    version:Mapped[str]=mapped_column(String(40),primary_key=True); applied_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))
class Report(Base):
    __tablename__="reports"
    id:Mapped[int]=mapped_column(primary_key=True); user_id:Mapped[int]=mapped_column(ForeignKey("users.id")); review_id:Mapped[Optional[int]]=mapped_column(ForeignKey("reviews.id"),nullable=True); seller_id:Mapped[Optional[int]]=mapped_column(ForeignKey("users.id"),nullable=True,index=True); reason:Mapped[str]=mapped_column(String(500)); status:Mapped[str]=mapped_column(String(20),default="open"); created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))
class ResetToken(Base):
    __tablename__="password_reset_tokens"
    id:Mapped[int]=mapped_column(primary_key=True); user_id:Mapped[int]=mapped_column(ForeignKey("users.id"),index=True); token_hash:Mapped[str]=mapped_column(String(64),unique=True); expires_at:Mapped[datetime]=mapped_column(DateTime(timezone=True)); used:Mapped[bool]=mapped_column(Boolean,default=False)
class DeviceSeen(Base):
    __tablename__="device_seen"
    id:Mapped[int]=mapped_column(primary_key=True); device_hash:Mapped[str]=mapped_column(String(64),unique=True,index=True); first_user_id:Mapped[int]=mapped_column(ForeignKey("users.id")); device_name:Mapped[str]=mapped_column(String(160),default="Unknown device"); ip_address:Mapped[str]=mapped_column(String(45),default=""); first_seen:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc)); last_seen:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))
class TrafficView(Base):
    __tablename__="traffic_views"
    id:Mapped[int]=mapped_column(primary_key=True); visitor_hash:Mapped[str]=mapped_column(String(64),index=True); page_type:Mapped[str]=mapped_column(String(24),index=True); viewed_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc),index=True); last_seen_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc),index=True); latest_page:Mapped[str]=mapped_column(String(24),default=""); device_name:Mapped[str]=mapped_column(String(160),default="Unknown device"); ip_address:Mapped[str]=mapped_column(String(45),default=""); user_id:Mapped[Optional[int]]=mapped_column(ForeignKey("users.id"),nullable=True,index=True)
    __table_args__=(Index("uq_traffic_views_visitor_hash","visitor_hash",unique=True),)

class LoginAttempt(Base):
    __tablename__="login_attempts"
    id:Mapped[int]=mapped_column(primary_key=True); ip_hash:Mapped[str]=mapped_column(String(64),index=True); attempted_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc),index=True)
class AdminSession(Base):
    __tablename__="admin_sessions"
    id:Mapped[str]=mapped_column(String(64),primary_key=True); admin_id:Mapped[int]=mapped_column(ForeignKey("users.id"),index=True); expires_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),index=True); last_activity:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc)); revoked_at:Mapped[Optional[datetime]]=mapped_column(DateTime(timezone=True),nullable=True)
Base.metadata.create_all(engine)
with engine.begin() as _migration_conn:
    _migration_conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS totp_secret_ciphertext TEXT"))
    _migration_conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS totp_enabled BOOLEAN NOT NULL DEFAULT FALSE"))
    _migration_conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS account_status VARCHAR(16) NOT NULL DEFAULT 'active'"))
    _migration_conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS account_status_note TEXT NOT NULL DEFAULT ''"))
    _migration_conn.execute(text("ALTER TABLE listings ADD COLUMN IF NOT EXISTS pincode VARCHAR(10) NOT NULL DEFAULT ''"))
    _migration_conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS google_sub VARCHAR(255)"))
    _migration_conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS photo_url VARCHAR(500) NOT NULL DEFAULT ''"))
    _migration_conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS referred_by_code VARCHAR(40) NOT NULL DEFAULT ''"))
    _migration_conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS terms_accepted_at TIMESTAMPTZ"))
    _migration_conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS privacy_accepted_at TIMESTAMPTZ"))
    _migration_conn.execute(text("ALTER TABLE device_seen ADD COLUMN IF NOT EXISTS device_name VARCHAR(160) NOT NULL DEFAULT 'Unknown device'"))
    _migration_conn.execute(text("ALTER TABLE device_seen ADD COLUMN IF NOT EXISTS ip_address VARCHAR(45) NOT NULL DEFAULT ''"))
    _migration_conn.execute(text("ALTER TABLE traffic_views ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"))
    _migration_conn.execute(text("ALTER TABLE traffic_views ADD COLUMN IF NOT EXISTS latest_page VARCHAR(24) NOT NULL DEFAULT ''"))
    _migration_conn.execute(text("ALTER TABLE traffic_views ADD COLUMN IF NOT EXISTS device_name VARCHAR(160) NOT NULL DEFAULT 'Unknown device'"))
    _migration_conn.execute(text("ALTER TABLE traffic_views ADD COLUMN IF NOT EXISTS ip_address VARCHAR(45) NOT NULL DEFAULT ''"))
    _migration_conn.execute(text("ALTER TABLE traffic_views ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id)"))
    _migration_conn.execute(text("ALTER TABLE reports ALTER COLUMN review_id DROP NOT NULL"))
    _migration_conn.execute(text("ALTER TABLE reports ADD COLUMN IF NOT EXISTS seller_id INTEGER REFERENCES users(id)"))
    _migration_conn.execute(text("ALTER TABLE reports ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"))
    _migration_conn.execute(text("CREATE INDEX IF NOT EXISTS ix_traffic_views_user_id ON traffic_views (user_id)"))
    _migration_conn.execute(text("UPDATE traffic_views SET last_seen_at = viewed_at, latest_page = page_type WHERE latest_page = ''"))
    _migration_conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_users_google_sub ON users (google_sub) WHERE google_sub IS NOT NULL"))
    _migration_conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_users_phone_nonempty ON users (phone) WHERE phone <> ''"))
    _migration_conn.execute(text("DELETE FROM traffic_views WHERE id NOT IN (SELECT MIN(id) FROM traffic_views GROUP BY visitor_hash)"))
    _migration_conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_traffic_views_visitor_hash ON traffic_views (visitor_hash)"))
with SessionLocal() as _migration_session:
    if not _migration_session.get(SchemaMigration,"001_initial"):
        _migration_session.add(SchemaMigration(version="001_initial")); _migration_session.commit()
    if not _migration_session.get(SchemaMigration,"002_listing_pincode"):
        _migration_session.add(SchemaMigration(version="002_listing_pincode")); _migration_session.commit()
    if not _migration_session.get(SchemaMigration,"003_unique_phone_device_count"):
        _migration_session.add(SchemaMigration(version="003_unique_phone_device_count")); _migration_session.commit()
ph=PasswordHash.recommended()
DUMMY_PASSWORD_HASH=ph.hash(secrets.token_urlsafe(32))
def dbdep():
    s=SessionLocal()
    try: yield s
    finally: s.close()
def seed():
    with SessionLocal() as s:
        if s.scalar(select(Listing.id)) is None:
            seller=User(email="demo.seller@example.com",name="Meera Patel",phone="919876543210",password_hash=ph.hash(secrets.token_urlsafe()),role="buyer"); s.add(seller); s.flush()
            for name,cat,city,price,moq,unit,lat,lon,img in [
                ("Premium Basmati Rice","Grains & Staples","Delhi",92,25,"kg",28.6139,77.2090,"rice"),("Alphonso Mango Crates","Fresh Produce","Mumbai",180,10,"kg",19.0760,72.8777,"mango"),("Organic Turmeric Powder","Spices","Jaipur",240,5,"kg",26.9124,75.7873,"turmeric"),("Cotton Tote Bags","Textiles","Ahmedabad",38,100,"pieces",23.0225,72.5714,"tote")]:
                s.add(Listing(owner_id=seller.id,seller_name=seller.name,business_name="Patel Wholesale Traders",address="Wholesale Market, Main Road",city=city,phone=seller.phone,whatsapp=seller.phone,name=name,category=cat,description=f"Carefully sourced {name.lower()} for wholesale buyers. Consistent quality, reliable stock, and flexible repeat supply. Contact us for current availability and final pricing.",image=f"https://images.unsplash.com/photo-{ {'rice':'1586201375761-83865001e31c','mango':'1553279768-865429fa0078','turmeric':'1615485290382-441e4d049cb5','tote':'1590874103328-eac38a683ce7'}[img]}?auto=format&fit=crop&w=1000&q=85",price=price,moq=moq,unit=unit,quantity="In stock",lat=lat,lon=lon,status="approved",published=True,featured=(name.endswith("Rice"))))
            s.commit()
seed()

app=FastAPI(title="KhetX API",version="1.0.0",openapi_url="/api/v1/openapi.json",docs_url="/docs")
origins=os.getenv("CORS_ORIGINS","http://localhost:8080").split(",")
app.add_middleware(CORSMiddleware,allow_origins=origins,allow_credentials=True,allow_methods=["*"],allow_headers=["*"])
Path("/app/uploads").mkdir(exist_ok=True)
app.mount("/uploads",StaticFiles(directory="/app/uploads"),name="uploads")
def login_client_key(req:Request):
    address=req.headers.get("x-real-ip") or req.headers.get("x-forwarded-for", "").split(",")[0].strip() or (req.client.host if req.client else "unknown")
    ip_hash=hashlib.sha256(f"{JWT_SECRET}:{address}".encode()).hexdigest()
    lock_id=int.from_bytes(bytes.fromhex(ip_hash)[:8],"big",signed=True)
    return ip_hash,lock_id
def check_login_rate(ip_hash:str,lock_id:int,s:Session):
    s.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"),{"lock_id":lock_id})
    now=datetime.now(timezone.utc); since=now-timedelta(minutes=15)
    attempts=s.scalar(select(func.count(LoginAttempt.id)).where(LoginAttempt.ip_hash==ip_hash,LoginAttempt.attempted_at>=since)) or 0
    s.execute(text("DELETE FROM login_attempts WHERE attempted_at < :expiry"),{"expiry":now-timedelta(hours=24)})
    if attempts>=5:
        oldest=s.scalar(select(func.min(LoginAttempt.attempted_at)).where(LoginAttempt.ip_hash==ip_hash,LoginAttempt.attempted_at>=since))
        wait=max(1,int((oldest+timedelta(minutes=15)-now).total_seconds()))
        raise HTTPException(429,"Too many failed login attempts. Please wait before trying again.",headers={"Retry-After":str(wait)})
RATE={}
RATE_LIMIT_PER_MINUTE=max(1,int(os.getenv("RATE_LIMIT_PER_MINUTE","1000")))
@app.middleware("http")
async def rate_limit(req:Request,call_next):
    # Use the validated end-user address (Cloudflare's overwritten client-IP
    # header when proxied), not req.client.host, which is usually the shared
    # Nginx container address for every marketplace visitor.
    key=request_ip(req) or (req.client.host if req.client else "unknown")
    now=time.time(); entries=[x for x in RATE.get(key,[]) if now-x<60]
    if len(entries)>=RATE_LIMIT_PER_MINUTE:
        retry_after=max(1,int(60-(now-entries[0]))+1) if entries else 60
        RATE[key]=entries
        return __import__('starlette.responses').responses.JSONResponse({"detail":"Rate limit exceeded"},status_code=429,headers={"Retry-After":str(retry_after)})
    entries.append(now); RATE[key]=entries
    return await call_next(req)
def tok(u): return jwt.encode({"sub":str(u.id),"exp":datetime.now(timezone.utc)+timedelta(minutes=int(os.getenv("JWT_EXPIRE_MINUTES","720")))},JWT_SECRET,algorithm="HS256")
def admin_tok(u,session_id:str): return jwt.encode({"sub":str(u.id),"jti":session_id,"exp":datetime.now(timezone.utc)+timedelta(hours=12)},JWT_SECRET,algorithm="HS256")
def cookie_secure(): return os.getenv("ADMIN_COOKIE_SECURE","true").strip().lower() in {"1","true","yes"}
def set_session_cookie(response:Response,token:str,max_age:int): response.set_cookie(SESSION_COOKIE,token,max_age=max_age,httponly=True,secure=cookie_secure(),samesite="lax",path="/")
def current(req:Request,s:Session=Depends(dbdep)):
    cookie=req.cookies.get(SESSION_COOKIE)
    raw=cookie or req.headers.get("Authorization","").removeprefix("Bearer ")
    if not raw: raise HTTPException(401,"Login required")
    try: claims=jwt.decode(raw,JWT_SECRET,algorithms=["HS256"]); uid=int(claims["sub"])
    except Exception: raise HTTPException(401,"Invalid or expired session")
    u=s.get(User,uid)
    if not u: raise HTTPException(401,"Account unavailable")
    if u.role=="buyer" and u.account_status!="active": raise HTTPException(403,"Your account is suspended or blocked. Submit an appeal at /account/appeal.")
    session_id=claims.get("jti")
    if cookie and session_id:
        admin_session=s.get(AdminSession,str(session_id)); now=datetime.now(timezone.utc)
        if not admin_session or admin_session.admin_id!=uid or admin_session.revoked_at or admin_session.expires_at<=now: raise HTTPException(401,"Admin session expired. Please sign in again.")
        req.state.admin_session_id=admin_session.id
        if req.url.path!="/api/v1/auth/admin/me":
            admin_session.last_activity=now; admin_session.expires_at=now+timedelta(minutes=ADMIN_INACTIVITY_MINUTES); s.commit()
    return u
def optional_current(req:Request,s:Session=Depends(dbdep)):
    if not req.cookies.get(SESSION_COOKIE) and not req.headers.get("Authorization"): return None
    try: return current(req,s)
    except HTTPException as error:
        if error.status_code==401: return None
        raise
def admin(req:Request,u=Depends(current),s:Session=Depends(dbdep)):
    if u.role!="admin":
        session_id=getattr(req.state,"admin_session_id",None)
        if session_id:
            session=s.get(AdminSession,session_id)
            if session: session.revoked_at=datetime.now(timezone.utc); s.commit()
        raise HTTPException(403,"Admin access required")
    if not getattr(req.state,"admin_session_id",None): raise HTTPException(401,"Admin sign-in required")
    return u
def public_listing(l): return {"id":l.id,"seller_id":l.owner_id,"seller_name":l.business_name or l.seller_name,"contact_name":l.seller_name,"address":l.address,"city":l.city,"pincode":l.pincode,"phone":l.phone,"whatsapp":l.whatsapp,"name":l.name,"category":l.category,"description":l.description,"image":l.image,"price":l.price,"price_label":l.price_label,"moq":l.moq,"unit":l.unit,"quantity":l.quantity,"lat":l.lat,"lon":l.lon,"featured":l.featured,"created_at":l.created_at.isoformat()}
class Auth(BaseModel): model_config={"extra":"forbid"}; email:EmailStr; password:str=Field(min_length=8,max_length=128); terms_accepted:bool=False; privacy_accepted:bool=False
class AdminLogin(BaseModel): model_config={"extra":"forbid"}; identifier:str=Field(min_length=1,max_length=254); password:str=Field(min_length=1,max_length=128); otp:str=Field(default="",max_length=12)
class GoogleAuth(BaseModel): credential:str=Field(min_length=20,max_length=5000); terms_accepted:bool=False; privacy_accepted:bool=False
class Register(Auth): name:str=Field(min_length=2,max_length=120); phone:str=Field(min_length=7,max_length=24); referral_code:str=Field(default="",max_length=40)
class ReviewIn(BaseModel): rating:int=Field(ge=1,le=5); body:str=Field(min_length=8,max_length=1200)
class StatusIn(BaseModel): status:str; note:str=""
class BulkListingAction(BaseModel): listing_ids:list[int]=Field(min_length=1,max_length=100); action:str; note:str=""
class TrafficViewIn(BaseModel): visitor_id:str=Field(min_length=36,max_length=36); page_type:str=Field(min_length=1,max_length=24)
class BuyerAppealIn(BaseModel): email:EmailStr; message:str=Field(min_length=20,max_length=3000)
class BuyerAccountAction(BaseModel): status:str; note:str=Field(default="",max_length=1000)
class AppealDecision(BaseModel): decision:str; note:str=Field(default="",max_length=1000)
class ListingIn(BaseModel): seller_name:str=Field(min_length=2,max_length=120); business_name:str=""; address:str; city:str; pincode:str=Field(pattern=r"^[0-9]{6}$"); phone:str; whatsapp:str; name:str; category:str; description:str=""; image:str=""; price:Optional[float]=None; price_label:str=""; moq:int=Field(ge=1); unit:str; quantity:str; lat:Optional[float]=None; lon:Optional[float]=None
def phoneok(x): return re.sub(r"\D","",x)
def request_ip(req:Request)->str:
    # Cloudflare overwrites this header on proxied requests. Fall back to the
    # directly connected address; do not trust arbitrary X-Forwarded-For values.
    candidate=req.headers.get("cf-connecting-ip", "").strip()
    try: return str(ipaddress.ip_address(candidate))
    except ValueError: return req.client.host if req.client else ""
def device_label(req:Request)->str:
    ua=req.headers.get("user-agent", "")
    os_name="Android" if "Android" in ua else "iOS" if any(x in ua for x in ("iPhone","iPad","iPod")) else "Windows" if "Windows" in ua else "macOS" if "Macintosh" in ua else "Linux" if "Linux" in ua else "Unknown OS"
    browser="Edge" if "Edg/" in ua else "Chrome" if "Chrome/" in ua else "Firefox" if "Firefox/" in ua else "Safari" if "Safari/" in ua else "Unknown browser"
    form="tablet" if any(x in ua for x in ("iPad","Tablet")) else "mobile" if any(x in ua for x in ("Mobile","Android","iPhone")) else "computer"
    return f"{os_name} {form} · {browser}"[:160]
def xlsx_sheet(rows:list[list[str]])->str:
    def safe(value):
        return "".join(ch for ch in str(value) if ch in "\t\n\r" or ord(ch)>=32)
    body=[]
    for row_num,row in enumerate(rows,1):
        cells=[]
        for col_num,value in enumerate(row,1):
            col=""; n=col_num
            while n:
                n,rem=divmod(n-1,26); col=chr(65+rem)+col
            cells.append(f'<c r="{col}{row_num}" t="inlineStr"><is><t xml:space="preserve">{xml_escape(safe(value))}</t></is></c>')
        body.append(f'<row r="{row_num}">'+"".join(cells)+"</row>")
    return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'+"".join(body)+"</sheetData></worksheet>"
def security_export_xlsx(rows:list[list[str]],sheet_name:str)->bytes:
    workbook=BytesIO()
    with zipfile.ZipFile(workbook,"w",zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml",'<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        archive.writestr("_rels/.rels",'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr("xl/workbook.xml",f'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="{xml_escape(sheet_name[:31])}" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels",'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr("xl/worksheets/sheet1.xml",xlsx_sheet(rows))
    return workbook.getvalue()
def accept_policies(u:User,now:Optional[datetime]=None):
    accepted_at=now or datetime.now(timezone.utc)
    u.terms_accepted_at=accepted_at
    u.privacy_accepted_at=accepted_at
def require_policy_acceptance(terms:bool,privacy:bool):
    if not terms or not privacy: raise HTTPException(422,"Please accept the Terms of Use and Privacy Policy to continue")
@app.get("/api/v1/health/live")
def live(): return {"status":"ok"}
@app.get("/api/v1/health/ready")
def ready():
    with engine.connect() as c: c.execute(text("select 1"))
    return {"status":"ready"}
@app.post("/api/v1/auth/register")
def register(data:Register,response:Response,s:Session=Depends(dbdep)):
    require_policy_acceptance(data.terms_accepted,data.privacy_accepted)
    email=data.email.lower()
    if s.scalar(select(User).where(User.email==email)): raise HTTPException(409,"Email already registered")
    phone=phoneok(data.phone)
    if len(phone)<7: raise HTTPException(422,"Enter a valid phone number")
    if s.scalar(select(User).where(User.phone==phone)): raise HTTPException(409,"Phone number already registered")
    u=User(email=email,name=data.name,phone=phone,password_hash=ph.hash(data.password),role="buyer",referred_by_code=data.referral_code.strip().upper(),terms_accepted_at=datetime.now(timezone.utc),privacy_accepted_at=datetime.now(timezone.utc)); s.add(u)
    try: s.commit()
    except Exception:
        s.rollback(); raise HTTPException(409,"Email or phone number already registered")
    s.refresh(u)
    set_session_cookie(response,tok(u),int(os.getenv("JWT_EXPIRE_MINUTES","720"))*60)
    return {"user":{"id":u.id,"email":u.email,"name":u.name,"role":u.role}}
@app.post("/api/v1/auth/admin/login")
def admin_login(data:AdminLogin,req:Request,response:Response,s:Session=Depends(dbdep)):
    ip_hash,lock_id=login_client_key(req); check_login_rate(ip_hash,lock_id,s)
    identifier=data.identifier.strip().lower()
    candidates=s.scalars(select(User).where(User.role=="admin",or_(User.email==identifier,func.lower(User.name)==identifier))).all()
    u=candidates[0] if len(candidates)==1 else None
    valid=ph.verify(data.password,u.password_hash if u else DUMMY_PASSWORD_HASH)
    if not u or not valid:
        s.add(LoginAttempt(ip_hash=ip_hash)); s.commit()
        raise HTTPException(401,"Administrator username/email or password is incorrect")
    if not u.totp_secret_ciphertext:
        secret=pyotp.random_base32()
        u.totp_secret_ciphertext=TOTP_CIPHER.encrypt(secret.encode()).decode(); s.commit()
        return {"setup_required":True,"secret":secret,"otpauth_url":pyotp.TOTP(secret).provisioning_uri(name=u.email,issuer_name="KhetX Admin")}
    try: secret=TOTP_CIPHER.decrypt(u.totp_secret_ciphertext.encode()).decode()
    except (InvalidToken,ValueError): raise HTTPException(503,"Admin authenticator configuration is unavailable. Contact the system administrator.")
    if not data.otp:
        if not u.totp_enabled:
            return {"setup_required":True,"secret":secret,"otpauth_url":pyotp.TOTP(secret).provisioning_uri(name=u.email,issuer_name="KhetX Admin")}
        return {"requires_otp":True}
    if not re.fullmatch(r"\d{6}",data.otp) or not pyotp.TOTP(secret).verify(data.otp,valid_window=1):
        s.add(LoginAttempt(ip_hash=ip_hash)); s.commit()
        raise HTTPException(401,"The six-digit authenticator code is incorrect or expired")
    if not u.totp_enabled: u.totp_enabled=True
    s.execute(text("DELETE FROM login_attempts WHERE ip_hash = :ip_hash"),{"ip_hash":ip_hash})
    now=datetime.now(timezone.utc); session_id=secrets.token_urlsafe(32)
    s.add(AdminSession(id=session_id,admin_id=u.id,expires_at=now+timedelta(minutes=ADMIN_INACTIVITY_MINUTES),last_activity=now)); s.commit()
    set_session_cookie(response,admin_tok(u,session_id),12*60*60)
    return {"user":{"id":u.id,"email":u.email,"name":u.name,"role":u.role},"totp_enabled":True}
@app.post("/api/v1/auth/logout")
@app.post("/api/v1/auth/admin/logout")
def logout(req:Request,response:Response,s:Session=Depends(dbdep)):
    raw=req.cookies.get(SESSION_COOKIE)
    if raw:
        try:
            session_id=jwt.decode(raw,JWT_SECRET,algorithms=["HS256"]).get("jti")
            admin_session=s.get(AdminSession,str(session_id)) if session_id else None
            if admin_session and not admin_session.revoked_at:
                admin_session.revoked_at=datetime.now(timezone.utc); s.commit()
        except Exception: pass
    response.delete_cookie(SESSION_COOKIE,path="/",httponly=True,secure=cookie_secure(),samesite="lax")
    return {"logged_out":True}
@app.post("/api/v1/auth/login")
def login(data:Auth,req:Request,response:Response,s:Session=Depends(dbdep)):
    require_policy_acceptance(data.terms_accepted,data.privacy_accepted)
    ip_hash,lock_id=login_client_key(req)
    check_login_rate(ip_hash,lock_id,s)
    u=s.scalar(select(User).where(User.email==data.email.lower()))
    valid=ph.verify(data.password,u.password_hash if u else DUMMY_PASSWORD_HASH)
    if not u or not valid:
        s.add(LoginAttempt(ip_hash=ip_hash)); s.commit()
        raise HTTPException(401,"Email or password is incorrect")
    if u.role=="buyer" and u.account_status!="active": raise HTTPException(403,"Your account is suspended or blocked. Submit an appeal at /account/appeal.")
    if u.role=="admin": raise HTTPException(403,"Administrators must sign in at /admin/login")
    accept_policies(u)
    s.execute(text("DELETE FROM login_attempts WHERE ip_hash = :ip_hash"),{"ip_hash":ip_hash}); s.commit()
    set_session_cookie(response,tok(u),int(os.getenv("JWT_EXPIRE_MINUTES","720"))*60)
    return {"user":{"id":u.id,"email":u.email,"name":u.name,"role":u.role}}
@app.post("/api/v1/auth/google")
def google_login(data:GoogleAuth,req:Request,response:Response,s:Session=Depends(dbdep)):
    require_policy_acceptance(data.terms_accepted,data.privacy_accepted)
    client_id=os.getenv("GOOGLE_CLIENT_ID","").strip()
    if not client_id: raise HTTPException(503,"Google sign-in is not configured yet")
    if req.headers.get("X-Requested-With")!="XMLHttpRequest": raise HTTPException(400,"Invalid sign-in request")
    try: claims=google_id_token.verify_oauth2_token(data.credential,GoogleRequest(),client_id)
    except Exception: raise HTTPException(401,"Google sign-in could not be verified. Please try again.")
    email=str(claims.get("email","")).strip().lower()
    if not email or claims.get("email_verified") is not True: raise HTTPException(401,"Google must verify your email address")
    # Trust automatic email linking only for Gmail or a verified Google Workspace domain.
    if not (email.endswith("@gmail.com") or claims.get("hd")): raise HTTPException(401,"Use a Gmail or Google Workspace account for Google sign-in")
    subject=str(claims.get("sub", ""))
    if not subject: raise HTTPException(401,"Google account identifier is missing")
    u=s.scalar(select(User).where(User.google_sub==subject))
    if not u:
        u=s.scalar(select(User).where(User.email==email))
        if u:
            if u.google_sub and u.google_sub!=subject: raise HTTPException(409,"This account is linked to a different Google identity")
            u.google_sub=subject
        else:
            u=User(email=email,name=str(claims.get("name") or email.split("@",1)[0])[:120],phone="",password_hash=ph.hash(secrets.token_urlsafe(48)),role="buyer",google_sub=subject)
            s.add(u)
    if u.role=="admin": raise HTTPException(403,"Administrators must sign in at /admin/login")
    if u.role=="buyer" and u.account_status!="active": raise HTTPException(403,"Your account is suspended or blocked. Submit an appeal at /account/appeal.")
    accept_policies(u)
    try: s.commit()
    except Exception:
        s.rollback(); raise HTTPException(409,"Could not create or link this Google account")
    set_session_cookie(response,tok(u),int(os.getenv("JWT_EXPIRE_MINUTES","720"))*60)
    return {"user":{"id":u.id,"email":u.email,"name":u.name,"role":u.role}}
@app.get("/api/v1/auth/me")
def me(u=Depends(current)): return {"id":u.id,"email":u.email,"name":u.name,"phone":u.phone,"role":u.role,"photo_url":u.photo_url}
@app.get("/api/v1/auth/admin/me")
def admin_me(u=Depends(admin)): return {"id":u.id,"email":u.email,"name":u.name,"role":u.role}
@app.post("/api/v1/auth/profile-photo")
def update_profile_photo(data:dict,u=Depends(current),s:Session=Depends(dbdep)):
    photo_url=str(data.get("photo_url", ""))
    if photo_url and (not photo_url.startswith("/uploads/") or "/../" in photo_url or not Path("/app",photo_url.lstrip("/")).is_file()):
        raise HTTPException(422,"Choose a valid uploaded profile photo")
    old_photo=u.photo_url
    u.photo_url=photo_url
    s.commit()
    if old_photo and old_photo!=photo_url and old_photo.startswith("/uploads/"):
        uploads=Path("/app/uploads").resolve(); old_file=Path("/app",old_photo.lstrip("/")).resolve()
        if uploads in old_file.parents and old_file.is_file():
            try: old_file.unlink()
            except OSError: log.warning("Could not remove replaced profile photo for user %s",u.id)
    return {"photo_url":u.photo_url}
@app.delete("/api/v1/auth/profile-photo")
def delete_profile_photo(u=Depends(current),s:Session=Depends(dbdep)):
    """Remove the signed-in user's profile photo and delete its managed upload."""
    old_photo=u.photo_url or ""
    u.photo_url=""
    try:
        s.commit()
    except Exception:
        s.rollback()
        log.exception("Could not clear profile photo for user %s",u.id)
        raise HTTPException(500,"Could not remove profile photo. Please try again.")
    if old_photo.startswith("/uploads/"):
        uploads=Path("/app/uploads").resolve()
        old_file=Path("/app",old_photo.lstrip("/")).resolve()
        if uploads in old_file.parents and old_file.is_file():
            try:
                old_file.unlink()
            except OSError:
                # The database reference is already cleared; a stale orphan file
                # must not make the profile photo appear to remain on the account.
                log.warning("Could not remove profile photo file for user %s",u.id)
    return {"photo_url":""}
@app.post("/api/v1/auth/device")
def record_device(data:dict,req:Request,u=Depends(current),s:Session=Depends(dbdep)):
    raw=str(data.get("device_id", ""))
    try: device_id=str(uuid.UUID(raw))
    except ValueError: raise HTTPException(422,"Invalid device identifier")
    hashed=hashlib.sha256(device_id.encode()).hexdigest(); seen=s.scalar(select(DeviceSeen).where(DeviceSeen.device_hash==hashed)); now=datetime.now(timezone.utc)
    # Keep the IP captured when this device was first registered. Repeated
    # check-ins update activity time/device label but must not overwrite that
    # historical IP snapshot.
    if seen: seen.last_seen=now; seen.device_name=device_label(req)
    else: s.add(DeviceSeen(device_hash=hashed,first_user_id=u.id,device_name=device_label(req),ip_address=request_ip(req),first_seen=now,last_seen=now))
    s.commit(); return {"recorded":True}
@app.post("/api/v1/traffic/pageview")
def record_pageview(data:TrafficViewIn,req:Request,u=Depends(optional_current),s:Session=Depends(dbdep)):
    try: visitor_id=str(uuid.UUID(data.visitor_id))
    except ValueError: raise HTTPException(422,"Invalid visitor identifier")
    allowed={"home","catalog","product","seller","sell","mine","support","privacy","terms","how","about"}
    if data.page_type not in allowed: raise HTTPException(422,"Invalid page type")
    visitor_hash=hashlib.sha256(visitor_id.encode()).hexdigest()
    now=datetime.now(timezone.utc); address=request_ip(req); name=device_label(req)
    stmt=pg_insert(TrafficView).values(visitor_hash=visitor_hash,page_type=data.page_type,latest_page=data.page_type,device_name=name,ip_address=address,user_id=u.id if u else None,viewed_at=now,last_seen_at=now)
    s.execute(stmt.on_conflict_do_update(index_elements=[TrafficView.visitor_hash],set_={"latest_page":stmt.excluded.latest_page,"device_name":stmt.excluded.device_name,"ip_address":stmt.excluded.ip_address,"user_id":stmt.excluded.user_id,"last_seen_at":stmt.excluded.last_seen_at}))
    # Keep analytics counts, but age out identifying details after 90 days.
    cutoff=now-timedelta(days=90)
    s.execute(text("UPDATE traffic_views SET ip_address='', device_name='Expired visitor' WHERE last_seen_at < :cutoff AND ip_address <> ''"),{"cutoff":cutoff})
    s.execute(text("UPDATE device_seen SET ip_address='' WHERE last_seen < :cutoff AND ip_address <> ''"),{"cutoff":cutoff})
    s.commit(); return {"recorded":True}
@app.post("/api/v1/auth/password-reset")
def reset(data:dict,s:Session=Depends(dbdep)):
    host=os.getenv("SMTP_HOST","").strip()
    smtp_user=os.getenv("SMTP_USER","").strip()
    smtp_password=os.getenv("SMTP_PASSWORD","")
    # Fail before looking up the account so SMTP setup errors do not reveal whether an email is registered.
    if host and smtp_user and not smtp_password:
        log.error("Password reset email is unavailable: SMTP_PASSWORD is not configured")
        raise HTTPException(503,"Password reset email is temporarily unavailable. Please contact support.")
    u=s.scalar(select(User).where(User.email==str(data.get("email","")).lower()))
    # Generic response avoids account enumeration. Configure SMTP for deliverable production reset links.
    if u:
        token=secrets.token_urlsafe(36); digest=hashlib.sha256(token.encode()).hexdigest()
        s.add(ResetToken(user_id=u.id,token_hash=digest,expires_at=datetime.now(timezone.utc)+timedelta(minutes=30))); s.commit()
        reset_url=f"{os.getenv('PUBLIC_APP_URL','http://localhost:8081').rstrip('/')}/?reset_token={quote(token,safe='')}"
        if host:
            message=EmailMessage(); message["Subject"]="Reset your KhetX password"; message["From"]=os.getenv("SMTP_FROM","no-reply@khetx.local"); message["To"]=u.email
            message.set_content(f"We received a request to reset your KhetX password.\n\nOpen this link to choose a new password (valid for 30 minutes):\n{reset_url}\n\nIf you did not request this, you can ignore this email.")
            try:
                with smtplib.SMTP(host,int(os.getenv("SMTP_PORT","587")),timeout=10) as smtp:
                    if os.getenv("SMTP_STARTTLS","true").lower()=="true": smtp.starttls()
                    if smtp_user: smtp.login(smtp_user,smtp_password)
                    smtp.send_message(message)
            except Exception:
                log.exception("Unable to send password reset email")
        else: log.warning("Development password reset link for %s: %s",u.email,reset_url)
    return {"message":"If the account exists, password reset instructions will be sent."}
@app.post("/api/v1/auth/password-reset/confirm")
def reset_confirm(data:dict,s:Session=Depends(dbdep)):
    token=str(data.get("token","")); password=str(data.get("password",""))
    if len(password)<8 or len(password)>128: raise HTTPException(422,"Password must be 8 to 128 characters")
    digest=hashlib.sha256(token.encode()).hexdigest(); rec=s.scalar(select(ResetToken).where(ResetToken.token_hash==digest,ResetToken.used==False))
    if not rec or rec.expires_at<datetime.now(timezone.utc): raise HTTPException(400,"Reset token is invalid or expired")
    u=s.get(User,rec.user_id); u.password_hash=ph.hash(password); rec.used=True; s.commit()
    return {"message":"Password updated. You can now log in."}
@app.get("/api/v1/config")
def config(): return {"support_whatsapp":os.getenv("SUPPORT_WHATSAPP","YOUR_WHATSAPP_NUMBER"),"support_email":os.getenv("SUPPORT_EMAIL","main@khetx.in"),"google_client_id":os.getenv("GOOGLE_CLIENT_ID","").strip()}
@app.get("/api/v1/categories")
def categories(s:Session=Depends(dbdep)): return sorted(set(s.scalars(select(Listing.category).where(Listing.published==True,Listing.status=="approved")).all()))
@app.get("/api/v1/products")
def products(q:str="",category:str="",city:str="",sort:str="recent",featured:bool=False,s:Session=Depends(dbdep),u=Depends(optional_current)):
    stmt=select(Listing).where(Listing.published==True,Listing.status=="approved")
    q=q.strip()
    if q:
        pattern="%"+q.replace("\\","\\\\").replace("%","\\%").replace("_","\\_")+"%"
        stmt=stmt.where(or_(Listing.name.ilike(pattern,escape="\\"),Listing.description.ilike(pattern,escape="\\"),Listing.business_name.ilike(pattern,escape="\\"),Listing.seller_name.ilike(pattern,escape="\\"),Listing.category.ilike(pattern,escape="\\"),Listing.city.ilike(pattern,escape="\\")))
    if category: stmt=stmt.where(Listing.category==category)
    if city: stmt=stmt.where(func.lower(Listing.city).contains(city.lower()))
    if featured: stmt=stmt.where(Listing.featured==True)
    if sort in ("price", "price_asc"): stmt=stmt.order_by(Listing.featured.desc(),Listing.price.asc().nulls_last(),Listing.created_at.desc())
    elif sort=="price_desc": stmt=stmt.order_by(Listing.featured.desc(),Listing.price.desc().nulls_last(),Listing.created_at.desc())
    else: stmt=stmt.order_by(Listing.featured.desc(),Listing.created_at.desc())
    matches=s.scalars(stmt.limit(100)).all()
    # A text search also promotes featured products from categories represented in
    # the matches, so a search for one vegetable can surface another featured one.
    related_categories={x.category for x in matches}
    if category: related_categories.add(category)
    related=[]
    if q and not featured and related_categories:
        featured_stmt=select(Listing).where(Listing.published==True,Listing.status=="approved",Listing.featured==True,Listing.category.in_(related_categories))
        if city: featured_stmt=featured_stmt.where(func.lower(Listing.city).contains(city.lower()))
        related=s.scalars(featured_stmt.order_by(Listing.created_at.desc()).limit(100)).all()
    rows=related+[x for x in matches if x.id not in {r.id for r in related}]
    result=[public_listing(x) for x in rows[:100]]
    # Public seller scores: only visible buyer-submitted reviews; no buyer coordinates are retained.
    for p in result:
        vals=s.execute(select(func.avg(Review.rating),func.count(Review.id)).where(Review.seller_id==p["seller_id"],Review.status=="visible")).one(); p["rating"]=round(float(vals[0] or 0),1); p["review_count"]=vals[1]
        if not u:
            for field in ("phone","whatsapp","address","pincode"): p.pop(field,None)
    return result
@app.get("/api/v1/products/{pid}")
def product(pid:int,s:Session=Depends(dbdep),u=Depends(current)):
    p=s.get(Listing,pid)
    if not p or not p.published or p.status!="approved": raise HTTPException(404,"Listing not found")
    out=public_listing(p); out["rating"],out["review_count"]=s.execute(select(func.avg(Review.rating),func.count(Review.id)).where(Review.seller_id==p.owner_id,Review.status=="visible")).one(); out["rating"]=round(float(out["rating"] or 0),1)
    return out
@app.get("/api/v1/sellers/{sid}")
def seller(sid:int,s:Session=Depends(dbdep),u=Depends(current)):
    user=s.get(User,sid)
    if not user: raise HTTPException(404,"Seller not found")
    ls=s.scalars(select(Listing).where(Listing.owner_id==sid,Listing.status=="approved",Listing.published==True)).all()
    if not ls: raise HTTPException(404,"Seller not found")
    first=ls[0]; avg,count=s.execute(select(func.avg(Review.rating),func.count(Review.id)).where(Review.seller_id==sid,Review.status=="visible")).one()
    reviews=[{"id":r.id,"rating":r.rating,"body":r.body,"verified":r.verified,"created_at":r.created_at.isoformat()} for r in s.scalars(select(Review).where(Review.seller_id==sid,Review.status=="visible").order_by(Review.created_at.desc()))]
    return {"id":sid,"name":first.business_name or user.name,"contact_name":first.seller_name,"address":first.address,"city":first.city,"pincode":first.pincode,"phone":first.phone,"whatsapp":first.whatsapp,"rating":round(float(avg or 0),1),"review_count":count,"reviews":reviews,"products":[public_listing(x) for x in ls]}
def optimize_upload_image(raw:bytes)->bytes:
    try:
        with Image.open(BytesIO(raw)) as image:
            if image.format not in {"JPEG","PNG","WEBP"}: raise HTTPException(415,"The uploaded file is not a supported image")
            image.load()
            image=image.convert("RGB") if image.mode not in ("RGBA","LA") else image.convert("RGBA")
            optimized=BytesIO(); image.save(optimized,format="WEBP",quality=78,method=4)
        return optimized.getvalue()
    except HTTPException: raise
    except (UnidentifiedImageError,OSError,Image.DecompressionBombError): raise HTTPException(415,"Could not read this image file")
@app.post("/api/v1/uploads")
async def upload(file:UploadFile=File(...),u=Depends(current)):
    if file.content_type not in {"image/jpeg","image/png","image/webp"}: raise HTTPException(415,"Upload a JPG, PNG, or WebP image")
    async with UPLOAD_SEMAPHORE:
        raw=await file.read(5_000_001)
        if len(raw)>5_000_000: raise HTTPException(413,"Image must be 5 MB or smaller")
        raw=await asyncio.to_thread(optimize_upload_image,raw)
        name=f"{uuid.uuid4().hex}.webp"; Path("/app/uploads",name).write_bytes(raw)
        return {"url":f"/uploads/{name}"}
@app.post("/api/v1/submissions")
def submit(data:ListingIn,u=Depends(current),s:Session=Depends(dbdep)):
    if len(phoneok(data.phone))<7 or len(phoneok(data.whatsapp))<7: raise HTTPException(422,"Enter valid phone and WhatsApp numbers")
    l=Listing(**data.model_dump(),owner_id=u.id,status="pending",published=False); s.add(l); s.commit(); s.refresh(l); return {"id":l.id,"status":l.status,"message":"Listing request submitted for admin review."}
@app.get("/api/v1/submissions/mine")
def mine(u=Depends(current),s:Session=Depends(dbdep)):
    return [{"id":x.id,"name":x.name,"city":x.city,"category":x.category,"price":x.price,"price_label":x.price_label,"moq":x.moq,"unit":x.unit,"quantity":x.quantity,"status":x.status,"published":x.published,"note":x.note,"created_at":x.created_at.isoformat()} for x in s.scalars(select(Listing).where(Listing.owner_id==u.id).order_by(Listing.created_at.desc()))]
@app.get("/api/v1/submissions/{lid}")
def get_submission(lid:int,u=Depends(current),s:Session=Depends(dbdep)):
    l=s.get(Listing,lid)
    if not l or l.owner_id!=u.id: raise HTTPException(404,"Request not found")
    return {k:v for k,v in l.__dict__.items() if not k.startswith("_")}
@app.put("/api/v1/submissions/{lid}")
def edit_submission(lid:int,data:ListingIn,u=Depends(current),s:Session=Depends(dbdep)):
    l=s.get(Listing,lid)
    if not l or l.owner_id!=u.id: raise HTTPException(404,"Request not found")
    if l.status not in ("changes_requested","approved"): raise HTTPException(403,"Only approved listings or requests marked for changes can be edited")
    if len(phoneok(data.phone))<7 or len(phoneok(data.whatsapp))<7: raise HTTPException(422,"Enter valid phone and WhatsApp numbers")
    for k,v in data.model_dump().items(): setattr(l,k,v)
    l.status="pending"; l.note=""; l.published=False; l.featured=False; l.reviewer_id=None; l.reviewed_at=None
    s.add(Audit(actor_id=u.id,action="seller_listing_edited",entity="listing",entity_id=lid,note="Seller updated listing details; admin review required."))
    s.commit(); return {"status":"pending","published":False,"message":"Changes sent for admin review. The listing is hidden until approved and published again."}
@app.post("/api/v1/submissions/{lid}/reapply")
def reapply_submission(lid:int,u=Depends(current),s:Session=Depends(dbdep)):
    l=s.get(Listing,lid)
    if not l or l.owner_id!=u.id: raise HTTPException(404,"Request not found")
    if l.status!="suspended": raise HTTPException(403,"Only suspended products can be submitted for re-review")
    prior_note=l.note
    l.status="pending"; l.published=False; l.note=(f"Seller requested re-review. Previous moderation note: {prior_note}" if prior_note else "Seller requested re-review."); l.reviewer_id=None; l.reviewed_at=None
    s.add(Audit(actor_id=u.id,action="seller_reapplied",entity="listing",entity_id=lid,note="Seller requested re-review of a suspended listing.")); s.commit()
    return {"status":"pending","message":"Product sent to admin for re-review. It remains unpublished until approved and published."}
@app.get("/api/v1/admin/overview")
def overview(u=Depends(admin),s:Session=Depends(dbdep)):
    ls=s.scalars(select(Listing).order_by(Listing.created_at.desc())).all(); rv=s.scalars(select(Review).order_by(Review.created_at.desc())).all(); reports=s.scalars(select(Report).where(Report.status=="open")).all()
    now=datetime.now(timezone.utc); today=now-timedelta(days=1); week=now-timedelta(days=7)
    new_visitors_24h=s.scalar(select(func.count(TrafficView.id)).where(TrafficView.viewed_at>=today)) or 0
    new_visitors_7d=s.scalar(select(func.count(TrafficView.id)).where(TrafficView.viewed_at>=week)) or 0
    unique_visitors=s.scalar(select(func.count(TrafficView.id))) or 0
    top_pages=s.execute(select(TrafficView.page_type,func.count(TrafficView.id)).where(TrafficView.viewed_at>=week).group_by(TrafficView.page_type).order_by(func.count(TrafficView.id).desc()).limit(5)).all()
    visitors=s.scalars(select(TrafficView).where(TrafficView.user_id.is_(None),TrafficView.last_seen_at>=now-timedelta(days=90)).order_by(TrafficView.last_seen_at.desc()).limit(100)).all()
    devices_all=s.scalars(select(DeviceSeen).where(DeviceSeen.last_seen>=now-timedelta(days=90)).order_by(DeviceSeen.last_seen.desc())).all()
    devices=devices_all[:100]
    device_users={x.id:x for x in s.scalars(select(User).where(User.id.in_({d.first_user_id for d in devices_all}))).all()} if devices_all else {}
    unique_registered_buyers_seen=s.scalar(select(func.count(func.distinct(DeviceSeen.first_user_id))).join(User,User.id==DeviceSeen.first_user_id).where(DeviceSeen.last_seen>=now-timedelta(days=90),User.role=="buyer")) or 0
    latest_device_by_user={}
    for device in devices_all:
        latest_device_by_user.setdefault(device.first_user_id,device)
    signed_in_users=[{"id":uid,"name":device_users[uid].name,"email":device_users[uid].email,"phone":device_users[uid].phone,"role":device_users[uid].role,"status":device_users[uid].account_status,"created_at":device_users[uid].created_at.isoformat(),"last_seen":device.last_seen.isoformat(),"latest_device":device.device_name,"ip_at_first_seen":device.ip_address or "Unavailable"} for uid,device in latest_device_by_user.items() if uid in device_users]
    buyers=s.scalars(select(User).where(User.role=="buyer").order_by(User.created_at.desc()).limit(500)).all()
    appeals=s.scalars(select(BuyerAppeal).order_by(BuyerAppeal.created_at.desc()).limit(500)).all()
    report_details=[]
    for report in reports:
        reporter=s.get(User,report.user_id); target_seller_id=report.seller_id
        if not target_seller_id and report.review_id:
            reported_review=s.get(Review,report.review_id); target_seller_id=reported_review.seller_id if reported_review else None
        target_seller=s.get(User,target_seller_id) if target_seller_id else None
        report_details.append({"id":report.id,"report_type":"seller" if report.seller_id else "review","seller_id":target_seller_id,"seller_name":target_seller.name if target_seller else "Unknown seller","reporter_name":reporter.name if reporter else "Unknown buyer","reporter_email":reporter.email if reporter else "Unavailable","review_id":report.review_id,"reason":report.reason,"status":report.status,"created_at":report.created_at.isoformat()})
    return {"unique_devices":s.scalar(select(func.count(DeviceSeen.id))) or 0,"unique_registered_buyers_seen":unique_registered_buyers_seen,"buyers":[{"id":x.id,"name":x.name,"email":x.email,"phone":x.phone,"status":x.account_status,"status_note":x.account_status_note,"created_at":x.created_at.isoformat()} for x in buyers],"appeals":[{"id":x.id,"user_id":x.user_id,"email":x.email,"message":x.message,"status":x.status,"admin_note":x.admin_note,"created_at":x.created_at.isoformat()} for x in appeals],"traffic":{"unique_browsers":unique_visitors,"new_browsers_24h":new_visitors_24h,"new_browsers_7d":new_visitors_7d,"top_pages_7d":[{"page":page,"visitors":count} for page,count in top_pages],"recent_visitors":[{"device_name":x.device_name,"ip_address":x.ip_address or "Unavailable","latest_page":x.latest_page or x.page_type,"last_seen":x.last_seen_at.isoformat()} for x in visitors],"signed_in_users":signed_in_users,"signed_in_devices":[{"device_name":x.device_name,"ip_address":x.ip_address or "Unavailable","email":device_users[x.first_user_id].email if x.first_user_id in device_users else "Account unavailable","name":device_users[x.first_user_id].name if x.first_user_id in device_users else "Account unavailable","first_seen":x.first_seen.isoformat(),"last_seen":x.last_seen.isoformat()} for x in devices]},"pending":[{**public_listing(x),"status":x.status,"note":x.note,"owner_id":x.owner_id} for x in ls if x.status in ("pending","changes_requested")],"approved":[{**public_listing(x),"status":x.status,"published":x.published} for x in ls if x.status=="approved"],"rejected":[{**public_listing(x),"status":x.status,"note":x.note} for x in ls if x.status=="rejected"],"suspended":[{**public_listing(x),"status":x.status,"published":x.published} for x in ls if x.status=="suspended"],"reviews":[{"id":x.id,"seller_id":x.seller_id,"rating":x.rating,"body":x.body,"status":x.status} for x in rv],"reports":report_details}
@app.post("/api/v1/account/appeals")
def submit_buyer_appeal(data:BuyerAppealIn,s:Session=Depends(dbdep)):
    email=str(data.email).strip().lower(); buyer=s.scalar(select(User).where(User.email==email,User.role=="buyer"))
    if buyer and buyer.account_status!="active":
        s.add(BuyerAppeal(user_id=buyer.id,email=email,message=data.message.strip()))
        s.add(Audit(actor_id=None,action="buyer_appeal_submitted",entity="buyer",entity_id=buyer.id,note="Buyer submitted an account status appeal.")); s.commit()
    # Return the same response regardless of account state to avoid account enumeration.
    return {"message":"If this email belongs to an account eligible for appeal, your request has been sent for review."}
@app.put("/api/v1/admin/buyers/{buyer_id}/status")
def set_buyer_status(buyer_id:int,data:BuyerAccountAction,u=Depends(admin),s:Session=Depends(dbdep)):
    if data.status not in {"active","blocked","suspended"}: raise HTTPException(422,"Status must be active, blocked, or suspended")
    buyer=s.get(User,buyer_id)
    if not buyer or buyer.role!="buyer": raise HTTPException(404,"Registered buyer not found")
    if buyer.id==u.id: raise HTTPException(409,"Admins cannot change their own buyer status")
    previous=buyer.account_status; buyer.account_status=data.status; buyer.account_status_note=data.note.strip()
    s.add(Audit(actor_id=u.id,action=f"buyer_{data.status}",entity="buyer",entity_id=buyer.id,note=f"Previous status: {previous}. {buyer.account_status_note}")); s.commit()
    return {"id":buyer.id,"status":buyer.account_status}
@app.put("/api/v1/admin/buyer-appeals/{appeal_id}")
def decide_buyer_appeal(appeal_id:int,data:AppealDecision,u=Depends(admin),s:Session=Depends(dbdep)):
    if data.decision not in {"restore","uphold"}: raise HTTPException(422,"Decision must be restore or uphold")
    appeal=s.get(BuyerAppeal,appeal_id)
    if not appeal: raise HTTPException(404,"Appeal not found")
    if appeal.status!="open": raise HTTPException(409,"This appeal has already been reviewed")
    appeal.status="restored" if data.decision=="restore" else "upheld"; appeal.admin_note=data.note.strip(); appeal.reviewed_by=u.id; appeal.reviewed_at=datetime.now(timezone.utc)
    buyer=s.get(User,appeal.user_id) if appeal.user_id else None
    if data.decision=="restore" and buyer:
        buyer.account_status="active"; buyer.account_status_note=""
    s.add(Audit(actor_id=u.id,action=f"buyer_appeal_{appeal.status}",entity="buyer_appeal",entity_id=appeal.id,note=appeal.admin_note)); s.commit()
    return {"status":appeal.status,"buyer_status":buyer.account_status if buyer else None}
@app.get("/api/v1/admin/security/export.xlsx")
def export_security_devices(scope:str="visitors",period:str="90d",u=Depends(admin),s:Session=Depends(dbdep)):
    periods={"1h":timedelta(hours=1),"24h":timedelta(hours=24),"7d":timedelta(days=7),"90d":timedelta(days=90)}
    if scope not in {"visitors","registered"}: raise HTTPException(422,"Scope must be visitors or registered")
    if period not in periods: raise HTTPException(422,"Period must be 1h, 24h, 7d, or 90d")
    cutoff=datetime.now(timezone.utc)-periods[period]
    if scope=="visitors":
        visitors=s.scalars(select(TrafficView).where(TrafficView.user_id.is_(None),TrafficView.last_seen_at>=cutoff).order_by(TrafficView.last_seen_at.desc())).all()
        rows=[["Device","IP address","Latest page","First seen","Last seen"]]
        rows.extend([[x.device_name,x.ip_address or "Unavailable",x.latest_page or x.page_type,x.viewed_at.isoformat(),x.last_seen_at.isoformat()] for x in visitors])
        sheet_name="Visitor IPs"; filename=f"KhetX-visitor-devices-{period}.xlsx"
    else:
        devices=s.scalars(select(DeviceSeen).where(DeviceSeen.last_seen>=cutoff).order_by(DeviceSeen.last_seen.desc())).all()
        user_ids={d.first_user_id for d in devices}
        users={x.id:x for x in s.scalars(select(User).where(User.id.in_(user_ids))).all()} if user_ids else {}
        rows=[["Name","Email","Phone","Role","Account status","Registered at","Device","IP address at first seen","Device first seen","Device last seen"]]
        rows.extend([[users[d.first_user_id].name if d.first_user_id in users else "Account unavailable",users[d.first_user_id].email if d.first_user_id in users else "Account unavailable",users[d.first_user_id].phone if d.first_user_id in users else "",users[d.first_user_id].role if d.first_user_id in users else "",users[d.first_user_id].account_status if d.first_user_id in users else "",users[d.first_user_id].created_at.isoformat() if d.first_user_id in users else "",d.device_name,d.ip_address or "Unavailable",d.first_seen.isoformat(),d.last_seen.isoformat()] for d in devices])
        sheet_name="Registered Users"; filename=f"KhetX-registered-users-{period}.xlsx"
    payload=security_export_xlsx(rows,sheet_name)
    return Response(content=payload,media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",headers={"Content-Disposition":f'attachment; filename="{filename}"',"Cache-Control":"no-store"})
@app.post("/api/v1/admin/listings/bulk")
def bulk_listings(data:BulkListingAction,u=Depends(admin),s:Session=Depends(dbdep)):
    allowed={"approve","approve_feature","suspend","feature","unfeature","unsuspend"}
    if data.action not in allowed: raise HTTPException(422,"Invalid bulk action")
    now=datetime.now(timezone.utc); records={x.id:x for x in s.scalars(select(Listing).where(Listing.id.in_(set(data.listing_ids))))}
    updated=[]; skipped=[]
    for lid in dict.fromkeys(data.listing_ids):
        x=records.get(lid)
        if not x: skipped.append({"id":lid,"reason":"Listing not found"}); continue
        previous=x.status
        if data.action in ("approve","approve_feature"):
            if x.status not in {"pending","changes_requested","rejected"}: skipped.append({"id":lid,"reason":"Only pending, changes-requested, or rejected listings can be approved"}); continue
            x.status="approved"; x.published=False; x.note=data.note; x.featured=(data.action=="approve_feature"); x.reviewer_id=u.id; x.reviewed_at=now
        elif data.action=="suspend":
            if x.status=="suspended": skipped.append({"id":lid,"reason":"Already suspended"}); continue
            x.status="suspended"; x.published=False; x.note=data.note or "Suspended by admin."; x.reviewer_id=u.id; x.reviewed_at=now
        elif data.action=="feature":
            if x.status!="approved": skipped.append({"id":lid,"reason":"Only approved listings can be featured"}); continue
            x.featured=True
        elif data.action=="unfeature": x.featured=False
        elif data.action=="unsuspend":
            if x.status!="suspended": skipped.append({"id":lid,"reason":"Only suspended listings can be restored"}); continue
            x.status="approved"; x.published=False; x.note="Unsuspended by admin. Listing remains unpublished until an admin explicitly publishes it."; x.reviewer_id=u.id; x.reviewed_at=now
        s.add(Audit(actor_id=u.id,action=f"bulk_{data.action}",entity="listing",entity_id=lid,note=f"Bulk action; previous status: {previous}. {data.note}".strip()))
        updated.append(lid)
    s.commit()
    return {"updated":updated,"skipped":skipped}
@app.put("/api/v1/admin/listings/{lid}")
def edit_listing(lid:int,data:ListingIn,u=Depends(admin),s:Session=Depends(dbdep)):
    l=s.get(Listing,lid)
    if not l: raise HTTPException(404,"Listing not found")
    for k,v in data.model_dump().items(): setattr(l,k,v)
    s.add(Audit(actor_id=u.id,action="listing_edited",entity="listing",entity_id=lid)); s.commit()
    return {"id":l.id,"status":l.status,"published":l.published}
@app.put("/api/v1/admin/listings/{lid}/decision")
def decision(lid:int,data:StatusIn,u=Depends(admin),s:Session=Depends(dbdep)):
    if data.status not in {"changes_requested","rejected","approved"}: raise HTTPException(422,"Invalid moderation decision")
    l=s.get(Listing,lid)
    if not l: raise HTTPException(404,"Listing not found")
    l.status=data.status; l.published=False; l.note=data.note; l.reviewer_id=u.id; l.reviewed_at=datetime.now(timezone.utc); s.add(Audit(actor_id=u.id,action=data.status,entity="listing",entity_id=lid,note=data.note)); s.commit(); return {"status":l.status,"published":l.published}
@app.post("/api/v1/admin/listings/{lid}/publish")
def publish(lid:int,u=Depends(admin),s:Session=Depends(dbdep)):
    l=s.get(Listing,lid)
    if not l or l.status!="approved": raise HTTPException(409,"Listing must be approved before publication")
    l.published=True; s.add(Audit(actor_id=u.id,action="published",entity="listing",entity_id=lid)); s.commit(); return {"published":True}
@app.post("/api/v1/admin/listings/{lid}/unpublish")
def unpublish(lid:int,u=Depends(admin),s:Session=Depends(dbdep)):
    l=s.get(Listing,lid)
    if not l: raise HTTPException(404,"Listing not found")
    l.published=False; s.add(Audit(actor_id=u.id,action="unpublished",entity="listing",entity_id=lid)); s.commit(); return {"published":False}
@app.post("/api/v1/admin/listings/{lid}/suspend")
def suspend(lid:int,data:dict,u=Depends(admin),s:Session=Depends(dbdep)):
    l=s.get(Listing,lid)
    if not l: raise HTTPException(404,"Listing not found")
    l.published=False; l.status="suspended"; l.note=str(data.get("note","")); l.reviewer_id=u.id; l.reviewed_at=datetime.now(timezone.utc)
    s.add(Audit(actor_id=u.id,action="suspended",entity="listing",entity_id=lid,note=l.note)); s.commit(); return {"status":l.status,"published":False}
@app.post("/api/v1/admin/listings/{lid}/unsuspend")
def unsuspend(lid:int,u=Depends(admin),s:Session=Depends(dbdep)):
    l=s.get(Listing,lid)
    if not l: raise HTTPException(404,"Listing not found")
    if l.status!="suspended": raise HTTPException(409,"Only suspended listings can be restored")
    previous_note=l.note
    l.status="approved"; l.published=False; l.note="Unsuspended by admin. Listing remains unpublished until an admin explicitly publishes it."; l.reviewer_id=u.id; l.reviewed_at=datetime.now(timezone.utc)
    s.add(Audit(actor_id=u.id,action="unsuspended",entity="listing",entity_id=lid,note=previous_note)); s.commit()
    return {"status":l.status,"published":False,"message":"Listing restored as approved and unpublished. Publish it separately to show it in the marketplace."}
@app.post("/api/v1/admin/listings/{lid}/feature")
def feature(lid:int,data:dict,u=Depends(admin),s:Session=Depends(dbdep)):
    l=s.get(Listing,lid)
    if not l: raise HTTPException(404,"Listing not found")
    l.featured=bool(data.get("featured")); s.commit(); return {"featured":l.featured}
@app.post("/api/v1/reviews/{sid}")
def add_review(sid:int,data:ReviewIn,u=Depends(current),s:Session=Depends(dbdep)):
    if u.role!="buyer": raise HTTPException(403,"Buyer accounts can submit reviews")
    if not s.scalar(select(Listing.id).where(Listing.owner_id==sid,Listing.status=="approved",Listing.published==True)): raise HTTPException(404,"Seller not found")
    r=Review(seller_id=sid,buyer_id=u.id,rating=data.rating,body=data.body,verified=False); s.add(r); s.commit(); return {"status":"submitted","verified":False}
@app.post("/api/v1/reviews/{rid}/report")
def report(rid:int,data:dict,u=Depends(current),s:Session=Depends(dbdep)):
    if not s.get(Review,rid): raise HTTPException(404,"Review not found")
    reason=str(data.get("reason","")).strip()
    if len(reason)<10: raise HTTPException(422,"Please explain the report in at least 10 characters")
    s.add(Report(user_id=u.id,review_id=rid,reason=reason[:500])); s.commit(); return {"status":"received"}
@app.post("/api/v1/sellers/{sid}/report")
def report_seller(sid:int,data:dict,u=Depends(current),s:Session=Depends(dbdep)):
    if u.role!="buyer": raise HTTPException(403,"Only signed-in buyers can report a seller")
    if sid==u.id: raise HTTPException(409,"You cannot report your own seller account")
    seller=s.get(User,sid)
    if not seller or not s.scalar(select(Listing.id).where(Listing.owner_id==sid,Listing.status=="approved",Listing.published==True)):
        raise HTTPException(404,"Seller not found")
    reason=str(data.get("reason","")).strip()
    if len(reason)<10: raise HTTPException(422,"Please explain the report in at least 10 characters")
    existing=s.scalar(select(Report.id).where(Report.user_id==u.id,Report.seller_id==sid,Report.status=="open"))
    if existing: raise HTTPException(409,"You already have an open report for this seller")
    item=Report(user_id=u.id,seller_id=sid,reason=reason[:500]); s.add(item); s.flush()
    s.add(Audit(actor_id=u.id,action="seller_report_submitted",entity="seller",entity_id=sid,note=f"Report #{item.id} submitted.")); s.commit()
    return {"status":"received","report_id":item.id}
@app.put("/api/v1/admin/reports/{report_id}")
def decide_report(report_id:int,data:dict,u=Depends(admin),s:Session=Depends(dbdep)):
    status=str(data.get("status",""))
    if status not in {"reviewed","dismissed"}: raise HTTPException(422,"Status must be reviewed or dismissed")
    item=s.get(Report,report_id)
    if not item: raise HTTPException(404,"Report not found")
    if item.status!="open": raise HTTPException(409,"This report has already been reviewed")
    item.status=status; s.add(Audit(actor_id=u.id,action=f"report_{status}",entity="report",entity_id=item.id,note=str(data.get("note",""))[:500])); s.commit()
    return {"status":item.status}
@app.put("/api/v1/admin/reviews/{rid}")
def mod_review(rid:int,data:dict,u=Depends(admin),s:Session=Depends(dbdep)):
    r=s.get(Review,rid)
    if not r: raise HTTPException(404,"Review not found")
    r.status="hidden" if data.get("hidden") else "visible"; s.add(Audit(actor_id=u.id,action="review_moderated",entity="review",entity_id=rid)); s.commit(); return {"status":r.status}
@app.post("/api/v1/support")
def support(data:dict):
    if len(str(data.get("message","")))<10: raise HTTPException(422,"Please include a message")
    host=os.getenv("SMTP_HOST"); recipient=os.getenv("SUPPORT_EMAIL","")
    if host and "@" in recipient:
        message=EmailMessage(); message["Subject"]="KhetX support inquiry"; message["From"]=os.getenv("SMTP_FROM","no-reply@khetx.local"); message["To"]=recipient
        message.set_content(f"From: {str(data.get('name',''))[:120]} <{str(data.get('email',''))[:254]}>\n\n{str(data.get('message',''))[:5000]}")
        with smtplib.SMTP(host,int(os.getenv("SMTP_PORT","587")),timeout=10) as smtp:
            if os.getenv("SMTP_STARTTLS","true").lower()=="true": smtp.starttls()
            if os.getenv("SMTP_USER"): smtp.login(os.getenv("SMTP_USER"),os.getenv("SMTP_PASSWORD",""))
            smtp.send_message(message)
    else: log.info("Support inquiry received; configure SMTP_HOST and SUPPORT_EMAIL to deliver by email")
    return {"message":"Thanks. Your inquiry has been received. Please use the contact links if you need a direct reply."}
