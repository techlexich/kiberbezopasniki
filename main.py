from fastapi import FastAPI, HTTPException, Depends, status, Request
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, EmailStr 
from pydantic import field_validator
from passlib.context import CryptContext
from datetime import datetime, timedelta
from jose import JWTError, jwt  
from typing import Optional
import psycopg2
from psycopg2.extras import RealDictCursor
import os
from dotenv import load_dotenv
import locale

# Загрузка переменных окружения
load_dotenv()

# Установка локали (безопасный вариант)
try:
    locale.setlocale(locale.LC_ALL, 'en_US.UTF-8')
except locale.Error:
    locale.setlocale(locale.LC_ALL, '')

# Проверка обязательных переменных окружения
SECRET_KEY = os.getenv("SECRET_KEY")
AUTH_DATABASE_URL = os.getenv("AUTH_DATABASE_URL")
CONNECT_DATABASE_URL = os.getenv("CONNECT_DATABASE_URL")

if not SECRET_KEY:
    raise ValueError("SECRET_KEY не установлен в переменных окружения")
if not AUTH_DATABASE_URL:
    raise ValueError("AUTH_DATABASE_URL не установлен в переменных окружения")
if not CONNECT_DATABASE_URL:
    raise ValueError("CONNECT_DATABASE_URL не установлен в переменных окружения")

# Конфигурация
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# Подключение к базе данных auth
def get_db_auth():
    try:
        conn = psycopg2.connect(
            AUTH_DATABASE_URL,
            cursor_factory=RealDictCursor,
            client_encoding='utf-8'
        )
        yield conn
    except psycopg2.Error as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка подключения к базе данных: {str(e)}"
        )
    finally:
        conn.close()

# Подключение к базе данных connect
def get_db_connect():
    try:
        conn = psycopg2.connect(
            CONNECT_DATABASE_URL,
            cursor_factory=RealDictCursor,
            client_encoding='utf-8'
        )
        yield conn
    except psycopg2.Error as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка подключения к базе данных: {str(e)}"
        )
    finally:
        conn.close()

# Модели данных
class UserBase(BaseModel):
    email: EmailStr
    username: str

class UserCreate(UserBase):
    password: str
    password_repeat: str

    @field_validator('password_repeat')
    def passwords_match(cls, v, info):
        if 'password' in info.data and v != info.data['password']:
            raise ValueError('Пароли не совпадают')
        return v

class UserInDB(UserBase):
    id: int
    password_hash: str

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    username: Optional[str] = None

class ImagePostRequest(BaseModel):
    image_url: str
    description: Optional[str] = None

# Настройки безопасности
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

app = FastAPI()

# Подключение статических файлов
app.mount("/static", StaticFiles(directory="static"), name="static")

# Настройка шаблонов
templates = Jinja2Templates(directory="templates")

# Настройка CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Вспомогательные функции
def verify_password(plain_password: str, hashed_password: str):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str):
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

async def get_user(db, username: str):
    try:
        cur = db.cursor()
        cur.execute("SELECT * FROM users WHERE username = %s", (username,))
        user = cur.fetchone()
        return user
    finally:
        cur.close()

async def authenticate_user(db, username: str, password: str):
    user = await get_user(db, username)
    if not user:
        return False
    if not verify_password(password, user["password_hash"]):
        return False
    return user

async def get_current_user(db = Depends(get_db_auth), token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Неверные учетные данные",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
        token_data = TokenData(username=username)
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Ошибка проверки токена: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    user = await get_user(db, username=token_data.username)
    if user is None:
        raise credentials_exception
    return user

# Роуты
@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/register", response_model=UserBase)
async def register(user: UserCreate, db = Depends(get_db_auth)):
    try:
        cur = db.cursor()
        cur.execute(
            "SELECT * FROM users WHERE email = %s OR username = %s", 
            (user.email, user.username)
        )
        existing_user = cur.fetchone()
        
        if existing_user:
            raise HTTPException(
                status_code=400,
                detail="Email или имя пользователя уже заняты",
            )
        
        hashed_password = get_password_hash(user.password)
        
        cur.execute(
            "INSERT INTO users (email, username, password_hash) VALUES (%s, %s, %s) RETURNING id, email, username",
            (user.email, user.username, hashed_password)
        )
        new_user = cur.fetchone()
        db.commit()
        return new_user
    except psycopg2.Error as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка базы данных: {str(e)}"
        )
    finally:
        cur.close()

@app.post("/token", response_model=Token)
async def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(), 
    db = Depends(get_db_auth)
):
    user = await authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверное имя пользователя или пароль",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user["username"]},
        expires_delta=access_token_expires
    )
    
    return {"access_token": access_token, "token_type": "bearer"}

@app.get("/users/me", response_model=UserBase)
async def read_users_me(current_user: UserInDB = Depends(get_current_user)):
    return current_user

@app.post("/api/save-image-url")
async def save_image_url(post_data: ImagePostRequest, db = Depends(get_db_connect)):
    try:
        with db.cursor() as cur:
            cur.execute(
                """INSERT INTO posts (photo_url, user_id, created_at, description)
                   VALUES (%s, %s, %s, %s) RETURNING post_id""",
                (post_data.image_url, 1, datetime.utcnow(), post_data.description)
            )
            post_id = cur.fetchone()["post_id"]
            db.commit()
        
        return {"status": "success", "post_id": post_id, "image_url": post_data.image_url}
    
    except psycopg2.DatabaseError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# Запуск сервера
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)