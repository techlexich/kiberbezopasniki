from fastapi import FastAPI, HTTPException, Depends, status, Request
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel, EmailStr, field_validator
from passlib.context import CryptContext
from datetime import datetime, timedelta
from jose import JWTError, jwt
from typing import Optional
import psycopg2
from psycopg2.extras import RealDictCursor
import os
from dotenv import load_dotenv
from pathlib import Path
from starlette.middleware.base import BaseHTTPMiddleware

# Настройки
load_dotenv()
SECRET_KEY = os.getenv("SECRET_KEY")
DB_URL = os.getenv("AUTH_DATABASE_URL")
if not SECRET_KEY or not DB_URL:
    raise ValueError("Не заданы SECRET_KEY или DB_URL")

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates") if Path("templates").exists() else None

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Модели
class UserProfile(BaseModel):
    bio: str = ""
    avatar: str = "/default-avatar.jpg"

class User(BaseModel):
    id: int
    username: str
    email: str
    profile: UserProfile = UserProfile()

class UserCreate(BaseModel):
    email: EmailStr
    username: str
    password: str
    password_repeat: str

    @field_validator('password_repeat')
    def passwords_match(cls, v, info):
        if v != info.data['password']:
            raise ValueError('Пароли не совпадают')
        return v

class Token(BaseModel):
    access_token: str
    token_type: str

# Настройки авторизации
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# Подключение к БД
def get_db():
    conn = psycopg2.connect(
        dbname="auth_db_17at",
        user="auth_db_17at_user",
        password="eGFSjcl2O29bWJBCAcRdh4MIzjNFojNZ",
        host="dpg-d05lfk2li9vc738u4vb0-a.oregon-postgres.render.com",
        port="5432",
        cursor_factory=RealDictCursor
    )
    try:
        yield conn
    finally:
        conn.close()

# Вспомогательные функции
def create_access_token(data: dict):
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode({**data, "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)

async def get_user(db, username: str):
    with db.cursor() as cur:
        cur.execute("""
            SELECT id, username, email, password_hash, 
                   COALESCE(bio, '') as bio,
                   COALESCE(avatar_url, '/default-avatar.jpg') as avatar
            FROM users WHERE username = %s
        """, (username,))
        return cur.fetchone()

async def authenticate_user(db, username: str, password: str):
    user = await get_user(db, username)
    if not user or not pwd_context.verify(password, user["password_hash"]):
        return False
    return user

async def get_current_user(db=Depends(get_db), token: str=Depends(oauth2_scheme)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user = await get_user(db, payload["sub"])
        if not user:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        return user
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

# Роуты
@app.post("/register", response_model=User)
async def register(user: UserCreate, db=Depends(get_db)):
    with db.cursor() as cur:
        cur.execute("SELECT * FROM users WHERE email = %s OR username = %s", 
                   (user.email, user.username))
        if cur.fetchone():
            raise HTTPException(400, "Email или username уже заняты")
        
        hashed = pwd_context.hash(user.password)
        cur.execute("""
            INSERT INTO users (email, username, password_hash)
            VALUES (%s, %s, %s)
            RETURNING id, username, email
        """, (user.email, user.username, hashed))
        new_user = cur.fetchone()
        db.commit()
        return {**new_user, "profile": {"bio": "", "avatar": "/default-avatar.jpg"}}

@app.post("/token", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm=Depends(), db=Depends(get_db)):
    user = await authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(401, "Неверный логин или пароль")
    return {
        "access_token": create_access_token({"sub": user["username"]}),
        "token_type": "bearer"
    }

@app.get("/users/me", response_model=User)
async def read_me(user=Depends(get_current_user)):
    return {
        "id": user["id"],
        "username": user["username"],
        "email": user["email"],
        "profile": {
            "bio": user["bio"],
            "avatar": user["avatar"]
        }
    }

@app.get("/users/{username}", response_model=User)
async def get_user_profile(username: str, db=Depends(get_db)):
    user = await get_user(db, username)
    if not user:
        raise HTTPException(404, "User not found")
    return {
        "id": user["id"],
        "username": user["username"],
        "email": user["email"],
        "profile": {
            "bio": user["bio"],
            "avatar": user["avatar"]
        }
    }

@app.put("/users/{user_id}", response_model=User)
async def update_profile(
    user_id: int, 
    profile: UserProfile,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    if current_user["id"] != user_id:
        raise HTTPException(403, "Forbidden")
    
    with db.cursor() as cur:
        cur.execute("""
            UPDATE users 
            SET bio = %s, avatar_url = %s
            WHERE id = %s
            RETURNING id, username, email, 
                     COALESCE(bio, '') as bio,
                     COALESCE(avatar_url, '/default-avatar.jpg') as avatar
        """, (profile.bio, profile.avatar, user_id))
        updated = cur.fetchone()
        db.commit()
        return {
            "id": updated["id"],
            "username": updated["username"],
            "email": updated["email"],
            "profile": {
                "bio": updated["bio"],
                "avatar": updated["avatar"]
            }
        }

@app.get("/profile/{username}")
async def profile_page(username: str, db=Depends(get_db)):
    # Проверяем существование пользователя
    user = await get_user(db, username)
    if not user:
        raise HTTPException(status_code=404)
    return FileResponse("static/profile.html")

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    if not templates:
        raise HTTPException(500, "Templates not found")
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/tape", response_class=HTMLResponse)
async def tape(request: Request):
    if not templates:
        raise HTTPException(500, "Templates not found")
    return templates.TemplateResponse("tape.html", {"request": request})

class NotFoundMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if response.status_code == 404:
            return FileResponse("static/404.html")
        return response

app.add_middleware(NotFoundMiddleware)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000) #111