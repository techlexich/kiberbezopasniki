from fastapi import FastAPI, HTTPException, Depends, status, Request
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel, EmailStr, field_validator, AnyUrl
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
from pydantic import Field
import logging
import boto3
from botocore.exceptions import NoCredentialsError
from fastapi import UploadFile, File, Form
import uuid  # Для генерации уникальных имен файлов

# Настройки
load_dotenv()
SECRET_KEY = os.getenv("SECRET_KEY")
DB_URL = os.getenv("AUTH_DATABASE_URL")
if not SECRET_KEY or not DB_URL:
    raise ValueError("Не заданы SECRET_KEY или DB_URL")

s3 = boto3.client(
    's3',
    aws_access_key_id=os.getenv("BEGET_S3_ACCESS_KEY"),
    aws_secret_access_key=os.getenv("BEGET_S3_SECRET_KEY"),
    endpoint_url=os.getenv("BEGET_S3_ENDPOINT"),  # Важно: добавьте endpoint
    region_name='ru-1'  # Или другой регион, указанный в beget
)

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Модели
class UserProfile(BaseModel):
    bio: str = Field(default="", max_length=500)
    avatar: str = Field(default="/default-avatar.jpg")

    @field_validator('avatar')
    def validate_avatar(cls, v):
        if not v.startswith(('http://', 'https://', '/')):
            raise ValueError("Avatar должен быть URL или начинаться с /")
        return v

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
async def read_me(request: Request, user=Depends(get_current_user)):
    avatar = user["avatar"]
    if avatar.startswith('/'):
        avatar = str(request.base_url)[:-1] + avatar
        
    return {
        "id": user["id"],
        "username": user["username"],
        "email": user["email"],
        "profile": {
            "bio": user["bio"],
            "avatar": avatar
        }
    }

@app.get("/users/{username}", response_model=User)
async def get_user_profile(username: str, request: Request, db=Depends(get_db)):
    user = await get_user(db, username)
    if not user:
        raise HTTPException(404, "User not found")
    
    avatar = user["avatar"]
    if avatar.startswith('/'):
        avatar = str(request.base_url)[:-1] + avatar
        
    return {
        "id": user["id"],
        "username": user["username"],
        "email": user["email"],
        "profile": {
            "bio": user["bio"],
            "avatar": avatar
        }
    }

logger = logging.getLogger(__name__)
@app.put("/users/{username}", response_model=User)
async def update_profile(
    username: str,
    profile: UserProfile,
    request: Request,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    # Проверяем, что текущий пользователь редактирует СВОЙ профиль
    if current_user["username"] != username:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Вы можете редактировать только свой профиль"
        )
    
    with db.cursor() as cur:
        try:
            cur.execute("""
                UPDATE users 
                SET bio = %s, avatar_url = %s
                WHERE username = %s
                RETURNING id, username, email, 
                         COALESCE(bio, '') as bio,
                         COALESCE(avatar_url, '/default-avatar.jpg') as avatar
            """, (profile.bio, profile.avatar, username))
            
            updated = cur.fetchone()
            if not updated:
                raise HTTPException(status_code=404, detail="Пользователь не найден")
            
            db.commit()
            logger.info(f"User {username} updated their profile")
            
            avatar = updated["avatar"]
            if avatar.startswith('/'):
                avatar = str(request.base_url)[:-1] + avatar
                
            return {
                "id": updated["id"],
                "username": updated["username"],
                "email": updated["email"],
                "profile": {
                    "bio": updated["bio"],
                    "avatar": avatar
                }
            }
            
        except Exception as e:
            db.rollback()
            logger.error(f"Error updating profile: {str(e)}")
            raise HTTPException(status_code=500, detail="Ошибка при обновлении профиля")

@app.get("/profile/{username}")
async def profile_page(username: str, db=Depends(get_db)):
    # Проверяем существование пользователя
    user = await get_user(db, username)
    if not user:
        raise HTTPException(status_code=404)
    return FileResponse("static/profile.html")

@app.get("/tape", response_class=HTMLResponse)
async def tape(request: Request):
    return templates.TemplateResponse("tape.html", {"request": request})

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/users/{username}/posts")
async def get_user_posts(
    username: str,
    skip: int = 0,
    limit: int = 10,
    db=Depends(get_db)
):
    with db.cursor() as cur:
        # Проверяем существование пользователя
        cur.execute("SELECT id FROM users WHERE username = %s", (username,))
        user = cur.fetchone()
        if not user:
            raise HTTPException(404, "User not found")
        
        # Получаем количество постов пользователя
        cur.execute("SELECT COUNT(*) FROM posts WHERE user_id = %s", (user["id"],))
        total_posts = cur.fetchone()["count"]
        
        # Получаем посты пользователя
        cur.execute("""
            SELECT p.*, u.username, u.avatar_url as user_avatar
            FROM posts p
            JOIN users u ON p.user_id = u.id
            WHERE p.user_id = %s
            ORDER BY p.created_at DESC
            LIMIT %s OFFSET %s
        """, (user["id"], limit, skip))
        posts = cur.fetchall()
        
        return {
            "posts": posts,
            "total": total_posts,
            "skip": skip,
            "limit": limit
        }

@app.delete("/posts/{post_id}")
async def delete_post(
    post_id: int,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    with db.cursor() as cur:
        # Проверяем, что пост существует и принадлежит пользователю
        cur.execute("""
            SELECT id, user_id, photo_url FROM posts WHERE id = %s
        """, (post_id,))
        post = cur.fetchone()
        
        if not post:
            raise HTTPException(404, "Post not found")
        
        if post["user_id"] != current_user["id"]:
            raise HTTPException(403, "You can delete only your own posts")
        
        # Удаляем файл из S3
        try:
            if post["photo_url"].startswith(os.getenv("BEGET_S3_ENDPOINT")):
                file_name = post["photo_url"].split('/')[-1]
                s3.delete_object(
                    Bucket=os.getenv("S3_BUCKET_NAME"),
                    Key=file_name
                )
        except Exception as e:
            logger.error(f"Error deleting file from S3: {str(e)}")
        
        # Удаляем пост из БД
        cur.execute("DELETE FROM posts WHERE id = %s RETURNING id", (post_id,))
        deleted_post = cur.fetchone()
        db.commit()
        
        if not deleted_post:
            raise HTTPException(500, "Failed to delete post")
        
        return {"status": "ok", "post_id": post_id}

@app.post("/posts")
async def create_post(
    photo: UploadFile = File(...),
    description: str = Form(default=""),
    shooting_time: Optional[str] = Form(None),
    location: Optional[str] = Form(None),
    camera_settings: Optional[str] = Form(None),
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    # Проверяем тип файла
    if not photo.content_type.startswith('image/'):
        raise HTTPException(400, "Only image files are allowed")
    
    # Проверяем размер файла (максимум 10MB)
    max_size = 10 * 1024 * 1024  # 10MB
    if photo.size > max_size:
        raise HTTPException(400, "File is too large (max 10MB)")
    
    try:
        # Генерируем уникальное имя файла
        file_ext = photo.filename.split('.')[-1].lower()
        if file_ext not in ['jpg', 'jpeg', 'png', 'gif']:
            raise HTTPException(400, "Unsupported file format")
            
        file_name = f"{uuid.uuid4()}.{file_ext}"
        
        # Загружаем в S3
        s3.upload_fileobj(
            photo.file,
            os.getenv("S3_BUCKET_NAME"),
            file_name,
            ExtraArgs={
                "ACL": "public-read",
                "ContentType": photo.content_type
            }
        )
        
        # Формируем URL
        photo_url = f"{os.getenv('BEGET_S3_ENDPOINT')}/{os.getenv('BEGET_S3_BUCKET_NAME')}/{file_name}"
        
        # Сохраняем в БД
        with db.cursor() as cur:
            cur.execute("""
                INSERT INTO posts 
                    (photo_url, description, user_id, shooting_time, location, camera_settings)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id, created_at
            """, (
                photo_url,
                description,
                current_user["id"],
                shooting_time,
                location,
                camera_settings
            ))
            new_post = cur.fetchone()
            db.commit()
        
        return {
            "status": "ok", 
            "post_id": new_post["id"],
            "created_at": new_post["created_at"],
            "photo_url": photo_url
        }
    
    except Exception as e:
        raise HTTPException(500, f"Ошибка загрузки: {str(e)}")

@app.get("/posts/{post_id}")
async def get_post(
    post_id: int,
    db=Depends(get_db)
):
    with db.cursor() as cur:
        cur.execute("""
            SELECT p.*, u.username, u.avatar_url as user_avatar
            FROM posts p
            JOIN users u ON p.user_id = u.id
            WHERE p.id = %s
        """, (post_id,))
        post = cur.fetchone()
        
        if not post:
            raise HTTPException(404, "Post not found")
        
        return post

@app.get("/check-s3-connection")
async def check_s3_connection():
    try:
        # Пробуем получить список бакетов
        response = s3.list_buckets()
        return {"status": "success", "buckets": [b['Name'] for b in response['Buckets']]}
    except Exception as e:
        raise HTTPException(500, f"S3 connection error: {str(e)}")

class NotFoundMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if response.status_code == 404:
            return FileResponse("static/404.html")
        return response

app.add_middleware(NotFoundMiddleware)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

#исключение ошибок
@app.exception_handler(500)
async def internal_server_error_handler(request: Request, exc: Exception):
    return PlainTextResponse("Internal Server Error", status_code=500)

@app.exception_handler(404)
async def not_found_handler(request: Request, exc: Exception):
    return FileResponse("static/404.html", status_code=404)