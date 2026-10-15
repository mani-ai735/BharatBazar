import os, re, uuid, logging, secrets, time
import hashlib, smtplib
from email.message import EmailMessage
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
import jwt
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import create_engine, String, Text, Float, Integer, Boolean, DateTime, ForeignKey, Index, func, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker, Session, relationship
from pwdlib import PasswordHash

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format='{"time":"%(asctime)s","level":"%(levelname)s","message":"%(message)s"}')
log=logging.getLogger("bazaar")
DB=os.getenv("DATABASE_URL","postgresql+psycopg://bazaar:bazaar@localhost:5432/bharat_bazaar")
engine=create_engine(DB,pool_pre_ping=True)
SessionLocal=sessionmaker(bind=engine,expire_on_commit=False)
class Base(DeclarativeBase): pass
class User(Base):
    __tablename__="users"
    id:Mapped[int]=mapped_column(primary_key=True); email:Mapped[str]=mapped_column(String(254),unique=True,index=True); name:Mapped[str]=mapped_column(String(120)); phone:Mapped[str]=mapped_column(String(24),default=""); password_hash:Mapped[str]=mapped_column(String(255)); role:Mapped[str]=mapped_column(String(16),default="buyer"); created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))
class Listing(Base):
    __tablename__="listings"
    id:Mapped[int]=mapped_column(primary_key=True); owner_id:Mapped[int]=mapped_column(ForeignKey("users.id"),index=True); seller_name:Mapped[str]=mapped_column(String(120)); business_name:Mapped[str]=mapped_column(String(160)); address:Mapped[str]=mapped_column(String(300)); city:Mapped[str]=mapped_column(String(120),index=True); phone:Mapped[str]=mapped_column(String(24)); whatsapp:Mapped[str]=mapped_column(String(24)); name:Mapped[str]=mapped_column(String(180),index=True); category:Mapped[str]=mapped_column(String(100),index=True); description:Mapped[str]=mapped_column(Text); image:Mapped[str]=mapped_column(String(500),default=""); price:Mapped[Optional[float]]=mapped_column(Float,nullable=True); price_label:Mapped[str]=mapped_column(String(100),default=""); moq:Mapped[int]=mapped_column(Integer); unit:Mapped[str]=mapped_column(String(40)); quantity:Mapped[str]=mapped_column(String(80)); lat:Mapped[Optional[float]]=mapped_column(Float,nullable=True); lon:Mapped[Optional[float]]=mapped_column(Float,nullable=True); status:Mapped[str]=mapped_column(String(24),default="pending",index=True); published:Mapped[bool]=mapped_column(Boolean,default=False,index=True); featured:Mapped[bool]=mapped_column(Boolean,default=False); note:Mapped[str]=mapped_column(Text,default=""); reviewer_id:Mapped[Optional[int]]=mapped_column(ForeignKey("users.id"),nullable=True); reviewed_at:Mapped[Optional[datetime]]=mapped_column(DateTime(timezone=True),nullable=True); created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))
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
    id:Mapped[int]=mapped_column(primary_key=True); user_id:Mapped[int]=mapped_column(ForeignKey("users.id")); review_id:Mapped[int]=mapped_column(ForeignKey("reviews.id")); reason:Mapped[str]=mapped_column(String(500)); status:Mapped[str]=mapped_column(String(20),default="open")
class ResetToken(Base):
    __tablename__="password_reset_tokens"
    id:Mapped[int]=mapped_column(primary_key=True); user_id:Mapped[int]=mapped_column(ForeignKey("users.id"),index=True); token_hash:Mapped[str]=mapped_column(String(64),unique=True); expires_at:Mapped[datetime]=mapped_column(DateTime(timezone=True)); used:Mapped[bool]=mapped_column(Boolean,default=False)

Base.metadata.create_all(engine)
with SessionLocal() as _migration_session:
    if not _migration_session.get(SchemaMigration,"001_initial"):
        _migration_session.add(SchemaMigration(version="001_initial")); _migration_session.commit()
ph=PasswordHash.recommended()
def dbdep():
    s=SessionLocal()
    try: yield s
    finally: s.close()
def seed():
    with SessionLocal() as s:
        email=os.getenv("SEED_ADMIN_EMAIL","admin@example.com").lower()
        if not s.scalar(select(User).where(User.email==email)):
            s.add(User(email=email,name="Marketplace Admin",password_hash=ph.hash(os.getenv("SEED_ADMIN_PASSWORD","ChangeMe123!")),role="admin")); s.commit()
        if s.scalar(select(Listing.id)) is None:
            seller=User(email="demo.seller@example.com",name="Meera Patel",phone="919876543210",password_hash=ph.hash(secrets.token_urlsafe()),role="buyer"); s.add(seller); s.flush()
            for name,cat,city,price,moq,unit,lat,lon,img in [
                ("Premium Basmati Rice","Grains & Staples","Delhi",92,25,"kg",28.6139,77.2090,"rice"),("Alphonso Mango Crates","Fresh Produce","Mumbai",180,10,"kg",19.0760,72.8777,"mango"),("Organic Turmeric Powder","Spices","Jaipur",240,5,"kg",26.9124,75.7873,"turmeric"),("Cotton Tote Bags","Textiles","Ahmedabad",38,100,"pieces",23.0225,72.5714,"tote")]:
                s.add(Listing(owner_id=seller.id,seller_name=seller.name,business_name="Patel Wholesale Traders",address="Wholesale Market, Main Road",city=city,phone=seller.phone,whatsapp=seller.phone,name=name,category=cat,description=f"Carefully sourced {name.lower()} for wholesale buyers. Consistent quality, reliable stock, and flexible repeat supply. Contact us for current availability and final pricing.",image=f"https://images.unsplash.com/photo-{ {'rice':'1586201375761-83865001e31c','mango':'1553279768-865429fa0078','turmeric':'1615485290382-441e4d049cb5','tote':'1590874103328-eac38a683ce7'}[img]}?auto=format&fit=crop&w=1000&q=85",price=price,moq=moq,unit=unit,quantity="In stock",lat=lat,lon=lon,status="approved",published=True,featured=(name.endswith("Rice"))))
            s.commit()
seed()

app=FastAPI(title="Bharat Bazaar API",version="1.0.0",openapi_url="/api/v1/openapi.json",docs_url="/docs")
origins=os.getenv("CORS_ORIGINS","http://localhost:8080").split(",")
app.add_middleware(CORSMiddleware,allow_origins=origins,allow_credentials=True,allow_methods=["*"],allow_headers=["*"])
Path("/app/uploads").mkdir(exist_ok=True)
app.mount("/uploads",StaticFiles(directory="/app/uploads"),name="uploads")
RATE={}
@app.middleware("http")
async def rate_limit(req:Request,call_next):
    key=req.client.host if req.client else "unknown"; now=time.time(); entries=[x for x in RATE.get(key,[]) if now-x<60]
    if len(entries)>=180: return __import__('starlette.responses').responses.JSONResponse({"detail":"Rate limit exceeded"},status_code=429)
    entries.append(now); RATE[key]=entries
    return await call_next(req)
def tok(u): return jwt.encode({"sub":str(u.id),"exp":datetime.now(timezone.utc)+timedelta(minutes=int(os.getenv("JWT_EXPIRE_MINUTES","720")))},os.getenv("JWT_SECRET","development-secret-change-this-please"),algorithm="HS256")
def current(req:Request,s:Session=Depends(dbdep)):
    raw=req.headers.get("Authorization","").removeprefix("Bearer ")
    if not raw: raise HTTPException(401,"Login required")
    try: uid=int(jwt.decode(raw,os.getenv("JWT_SECRET","development-secret-change-this-please"),algorithms=["HS256"])["sub"])
    except Exception: raise HTTPException(401,"Invalid or expired session")
    u=s.get(User,uid)
    if not u: raise HTTPException(401,"Account unavailable")
    return u
def admin(u=Depends(current)):
    if u.role!="admin": raise HTTPException(403,"Admin access required")
    return u
def public_listing(l): return {"id":l.id,"seller_id":l.owner_id,"seller_name":l.business_name or l.seller_name,"contact_name":l.seller_name,"address":l.address,"city":l.city,"phone":l.phone,"whatsapp":l.whatsapp,"name":l.name,"category":l.category,"description":l.description,"image":l.image,"price":l.price,"price_label":l.price_label,"moq":l.moq,"unit":l.unit,"quantity":l.quantity,"lat":l.lat,"lon":l.lon,"featured":l.featured,"created_at":l.created_at.isoformat()}
class Auth(BaseModel): email:EmailStr; password:str=Field(min_length=8,max_length=128)
class Register(Auth): name:str=Field(min_length=2,max_length=120); phone:str=Field(min_length=7,max_length=24)
class ReviewIn(BaseModel): rating:int=Field(ge=1,le=5); body:str=Field(min_length=8,max_length=1200)
class StatusIn(BaseModel): status:str; note:str=""
class ListingIn(BaseModel): seller_name:str; business_name:str=""; address:str; city:str; phone:str; whatsapp:str; name:str; category:str; description:str; image:str=""; price:Optional[float]=None; price_label:str=""; moq:int=Field(ge=1); unit:str; quantity:str; lat:Optional[float]=None; lon:Optional[float]=None
def phoneok(x): return re.sub(r"\D","",x)
@app.get("/api/v1/health/live")
def live(): return {"status":"ok"}
@app.get("/api/v1/health/ready")
def ready():
    with engine.connect() as c: c.execute(text("select 1"))
    return {"status":"ready"}
@app.post("/api/v1/auth/register")
def register(data:Register,s:Session=Depends(dbdep)):
    email=data.email.lower()
    if s.scalar(select(User).where(User.email==email)): raise HTTPException(409,"Email already registered")
    if len(phoneok(data.phone))<7: raise HTTPException(422,"Enter a valid phone number")
    u=User(email=email,name=data.name,phone=phoneok(data.phone),password_hash=ph.hash(data.password),role="buyer"); s.add(u); s.commit(); s.refresh(u)
    return {"token":tok(u),"user":{"id":u.id,"email":u.email,"name":u.name,"role":u.role}}
@app.post("/api/v1/auth/login")
def login(data:Auth,s:Session=Depends(dbdep)):
    u=s.scalar(select(User).where(User.email==data.email.lower()))
    if not u or not ph.verify(data.password,u.password_hash): raise HTTPException(401,"Email or password is incorrect")
    return {"token":tok(u),"user":{"id":u.id,"email":u.email,"name":u.name,"role":u.role}}
@app.get("/api/v1/auth/me")
def me(u=Depends(current)): return {"id":u.id,"email":u.email,"name":u.name,"role":u.role}
@app.post("/api/v1/auth/password-reset")
def reset(data:dict,s:Session=Depends(dbdep)):
    u=s.scalar(select(User).where(User.email==str(data.get("email","")).lower()))
    # Generic response avoids account enumeration. Configure SMTP for deliverable production reset links.
    if u:
        token=secrets.token_urlsafe(36); digest=hashlib.sha256(token.encode()).hexdigest()
        s.add(ResetToken(user_id=u.id,token_hash=digest,expires_at=datetime.now(timezone.utc)+timedelta(minutes=30))); s.commit()
        host=os.getenv("SMTP_HOST")
        if host:
            message=EmailMessage(); message["Subject"]="Reset your Bharat Bazaar password"; message["From"]=os.getenv("SMTP_FROM","no-reply@bharatbazaar.local"); message["To"]=u.email
            message.set_content(f"Reset your password with this one-time token: {token}\nIt expires in 30 minutes.")
            with smtplib.SMTP(host,int(os.getenv("SMTP_PORT","587")),timeout=10) as smtp:
                if os.getenv("SMTP_STARTTLS","true").lower()=="true": smtp.starttls()
                if os.getenv("SMTP_USER"): smtp.login(os.getenv("SMTP_USER"),os.getenv("SMTP_PASSWORD",""))
                smtp.send_message(message)
        else: log.warning("Development password reset token for %s: %s",u.email,token)
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
def config(): return {"support_whatsapp":os.getenv("SUPPORT_WHATSAPP","YOUR_WHATSAPP_NUMBER"),"support_email":os.getenv("SUPPORT_EMAIL","YOUR_SUPPORT_EMAIL")}
@app.get("/api/v1/categories")
def categories(s:Session=Depends(dbdep)): return sorted(set(s.scalars(select(Listing.category).where(Listing.published==True,Listing.status=="approved")).all()))
@app.get("/api/v1/products")
def products(q:str="",category:str="",city:str="",sort:str="recent",featured:bool=False,s:Session=Depends(dbdep),u=Depends(current)):
    stmt=select(Listing).where(Listing.published==True,Listing.status=="approved")
    if q: stmt=stmt.where(func.lower(Listing.name+" "+Listing.description+" "+Listing.business_name).contains(q.lower()))
    if category: stmt=stmt.where(Listing.category==category)
    if city: stmt=stmt.where(func.lower(Listing.city).contains(city.lower()))
    if featured: stmt=stmt.where(Listing.featured==True)
    if sort=="price": stmt=stmt.order_by(Listing.price.asc().nulls_last())
    else: stmt=stmt.order_by(Listing.created_at.desc())
    result=[public_listing(x) for x in s.scalars(stmt.limit(100)).all()]
    # Public seller scores: only visible buyer-submitted reviews; no buyer coordinates are retained.
    for p in result:
        vals=s.execute(select(func.avg(Review.rating),func.count(Review.id)).where(Review.seller_id==p["seller_id"],Review.status=="visible")).one(); p["rating"]=round(float(vals[0] or 0),1); p["review_count"]=vals[1]
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
    return {"id":sid,"name":first.business_name or user.name,"contact_name":first.seller_name,"address":first.address,"city":first.city,"phone":first.phone,"whatsapp":first.whatsapp,"rating":round(float(avg or 0),1),"review_count":count,"reviews":reviews,"products":[public_listing(x) for x in ls]}
@app.post("/api/v1/uploads")
async def upload(file:UploadFile=File(...),u=Depends(current)):
    if file.content_type not in {"image/jpeg","image/png","image/webp"}: raise HTTPException(415,"Upload a JPG, PNG, or WebP image")
    raw=await file.read(5_000_001)
    if len(raw)>5_000_000: raise HTTPException(413,"Image must be 5 MB or smaller")
    ext={"image/jpeg":"jpg","image/png":"png","image/webp":"webp"}[file.content_type]; name=f"{uuid.uuid4().hex}.{ext}"; Path("/app/uploads",name).write_bytes(raw)
    return {"url":f"/uploads/{name}"}
@app.post("/api/v1/submissions")
def submit(data:ListingIn,u=Depends(current),s:Session=Depends(dbdep)):
    if len(phoneok(data.phone))<7 or len(phoneok(data.whatsapp))<7: raise HTTPException(422,"Enter valid phone and WhatsApp numbers")
    l=Listing(**data.model_dump(),owner_id=u.id,status="pending",published=False); s.add(l); s.commit(); s.refresh(l); return {"id":l.id,"status":l.status,"message":"Listing request submitted for admin review."}
@app.get("/api/v1/submissions/mine")
def mine(u=Depends(current),s:Session=Depends(dbdep)):
    return [{"id":x.id,"name":x.name,"city":x.city,"status":x.status,"note":x.note,"created_at":x.created_at.isoformat()} for x in s.scalars(select(Listing).where(Listing.owner_id==u.id).order_by(Listing.created_at.desc()))]
@app.get("/api/v1/submissions/{lid}")
def get_submission(lid:int,u=Depends(current),s:Session=Depends(dbdep)):
    l=s.get(Listing,lid)
    if not l or l.owner_id!=u.id: raise HTTPException(404,"Request not found")
    return {k:v for k,v in l.__dict__.items() if not k.startswith("_")}
@app.put("/api/v1/submissions/{lid}")
def edit_submission(lid:int,data:ListingIn,u=Depends(current),s:Session=Depends(dbdep)):
    l=s.get(Listing,lid)
    if not l or l.owner_id!=u.id: raise HTTPException(404,"Request not found")
    if l.status!="changes_requested": raise HTTPException(403,"Only requests marked for changes can be edited")
    for k,v in data.model_dump().items(): setattr(l,k,v)
    l.status="pending"; l.note=""; l.published=False; s.commit(); return {"status":"pending"}
@app.get("/api/v1/admin/overview")
def overview(u=Depends(admin),s:Session=Depends(dbdep)):
    ls=s.scalars(select(Listing).order_by(Listing.created_at.desc())).all(); rv=s.scalars(select(Review).order_by(Review.created_at.desc())).all(); reports=s.scalars(select(Report).where(Report.status=="open")).all()
    return {"pending":[{**public_listing(x),"status":x.status,"note":x.note,"owner_id":x.owner_id} for x in ls if x.status in ("pending","changes_requested")],"approved":[{**public_listing(x),"status":x.status,"published":x.published} for x in ls if x.status=="approved"],"rejected":[{**public_listing(x),"status":x.status,"note":x.note} for x in ls if x.status=="rejected"],"suspended":[{**public_listing(x),"status":x.status,"published":x.published} for x in ls if x.status=="suspended"],"reviews":[{"id":x.id,"seller_id":x.seller_id,"rating":x.rating,"body":x.body,"status":x.status} for x in rv],"reports":[{"id":x.id,"review_id":x.review_id,"reason":x.reason} for x in reports]}
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
    s.add(Report(user_id=u.id,review_id=rid,reason=str(data.get("reason",""))[:500])); s.commit(); return {"status":"received"}
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
        message=EmailMessage(); message["Subject"]="Bharat Bazaar support inquiry"; message["From"]=os.getenv("SMTP_FROM","no-reply@bharatbazaar.local"); message["To"]=recipient
        message.set_content(f"From: {str(data.get('name',''))[:120]} <{str(data.get('email',''))[:254]}>\n\n{str(data.get('message',''))[:5000]}")
        with smtplib.SMTP(host,int(os.getenv("SMTP_PORT","587")),timeout=10) as smtp:
            if os.getenv("SMTP_STARTTLS","true").lower()=="true": smtp.starttls()
            if os.getenv("SMTP_USER"): smtp.login(os.getenv("SMTP_USER"),os.getenv("SMTP_PASSWORD",""))
            smtp.send_message(message)
    else: log.info("Support inquiry received; configure SMTP_HOST and SUPPORT_EMAIL to deliver by email")
    return {"message":"Thanks. Your inquiry has been received. Please use the contact links if you need a direct reply."}
