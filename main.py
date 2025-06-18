from fastapi import FastAPI, HTTPException, Depends, status, Request, UploadFile, File, Form
from fastapi.exceptions import RequestValidationError
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, FileResponse, PlainTextResponse, RedirectResponse, JSONResponse
from pydantic import BaseModel, EmailStr, field_validator, AnyUrl, Field
from passlib.context import CryptContext
from jose import JWTError, jwt
from typing import Optional
import psycopg2
from psycopg2.extras import RealDictCursor
import os
from dotenv import load_dotenv
from pathlib import Path
from datetime import datetime, timedelta
import logging
import boto3
from botocore.exceptions import NoCredentialsError, ClientError
import uuid
import requests
from requests_aws4auth import AWS4Auth
from requests.auth import HTTPBasicAuth
import hashlib
import base64
from slugify import slugify
from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS
import io
import random
import json
from botocore.config import Config
import hmac
from urllib.parse import urlparse

# Загрузка переменных окружения
load_dotenv()

# Конфигурация
SECRET_KEY = os.getenv("SECRET_KEY", "fallback-secret-key-for-dev")
DB_URL = os.getenv("AUTH_DATABASE_URL")
BEGET_S3_ENDPOINT = os.getenv("BEGET_S3_ENDPOINT", "")
BEGET_S3_BUCKET_NAME = os.getenv("BEGET_S3_BUCKET_NAME", "")
BEGET_S3_ACCESS_KEY = os.getenv("BEGET_S3_ACCESS_KEY", "")
BEGET_S3_SECRET_KEY = os.getenv("BEGET_S3_SECRET_KEY", "")
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY", "4c9c5126cac8583846104eb7825d1ae4")
CITY = os.getenv("CITY", "Ekaterinburg")

# Инициализация приложения
app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Настройка логгирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация S3
s3 = boto3.client(
    's3',
    endpoint_url=BEGET_S3_ENDPOINT,
    aws_access_key_id=BEGET_S3_ACCESS_KEY,
    aws_secret_access_key=BEGET_S3_SECRET_KEY,
    region_name='ru-1',
    config=boto3.session.Config(
        signature_version='s3v4',
        s3={'addressing_style': 'path'}
    )
)

# Модели данных
class CommentBase(BaseModel):
    content: str = Field(..., min_length=1, max_length=500)

class CommentCreate(CommentBase):
    pass

class CommentResponse(BaseModel):
    id: int
    post_id: int
    user_id: int
    username: str
    avatar: str
    content: str
    created_at: datetime
    updated_at: datetime

class PostBase(BaseModel):
    photo_url: str
    description: Optional[str] = None
    latitude: Optional[float] = None
    altitude: Optional[float] = None
    tags: Optional[str] = None

class PostResponse(BaseModel):
    id: int
    photo_url: str
    description: Optional[str] = None
    created_at: datetime
    likes_count: int
    comments_count: int
    username: str
    user_avatar: str
    is_liked: bool = False
    latitude: Optional[float] = None
    altitude: Optional[float] = None

class UserProfile(BaseModel):
    bio: str = Field(default="", max_length=500)
    avatar: str = Field(default="/static/default_avatar.jpg")

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
        dbname="auth_db_17at_90uu",
        user="auth_db_17at_user",
        password="rOK6TE8lX6zIisiF2E2siOmbGPnpUGxI",
        host="dpg-d0qvflje5dus739v4q50-a.oregon-postgres.render.com",
        port="5432",
        sslmode="require",
        cursor_factory=RealDictCursor   
    )
    try:
        yield conn
    finally:
        conn.close()

# Вспомогательные функции
def extract_exif_data(image_bytes: bytes) -> dict:
    try:
        image = Image.open(io.BytesIO(image_bytes))
        
        if not hasattr(image, '_getexif') or image._getexif() is None:
            return {}
        
        exif_data = {
            TAGS.get(tag, tag): value
            for tag, value in image._getexif().items()
        }
        
        if 'GPSInfo' in exif_data:
            gps_info = {
                GPSTAGS.get(tag, tag): value
                for tag, value in exif_data['GPSInfo'].items()
            }
            exif_data['GPSInfo'] = gps_info

        useful_data = {
            'camera_make': exif_data.get('Make', ''),
            'camera_model': exif_data.get('Model', ''),
            'exposure_time': str(exif_data.get('ExposureTime', '')),
            'f_number': f"f/{exif_data.get('FNumber', '')}" if exif_data.get('FNumber') else '',
            'iso': str(exif_data.get('ISOSpeedRatings', '')),
            'focal_length': str(exif_data.get('FocalLength', '')),
            'lens_model': exif_data.get('LensModel', ''),
            'date_time': exif_data.get('DateTimeOriginal', ''),
            'gps_info': exif_data.get('GPSInfo', {})
        }
        
        return {k: v for k, v in useful_data.items() if v}
        
    except Exception as e:
        logger.error(f"EXIF extraction error: {str(e)}")
        return {}

def create_access_token(data: dict):
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode({**data, "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)

async def get_user(db, username: str):
    with db.cursor() as cur:
        cur.execute("""
            SELECT id, username, email, password_hash, 
                   COALESCE(bio, '') as bio,
                   COALESCE(avatar_url, '/static/default_avatar.jpg') as avatar
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

async def get_current_user_optional(
    db=Depends(get_db),
    token: Optional[str] = Depends(oauth2_scheme)
):
    if not token:
        return None
    
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user = await get_user(db, payload["sub"])
        return user
    except JWTError:
        return None

# Роуты авторизации
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
        return {**new_user, "profile": {"bio": "", "avatar": "/static/default_avatar.jpg"}}

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

# Роуты постов
@app.post("/posts")
async def create_post(
    photo: UploadFile = File(...),
    description: str = Form(default=""),
    altitude: str = Form(...),
    latitude: str = Form(...),
    tags: str = Form(default=""),
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    if not photo.filename or not photo.content_type:
        raise HTTPException(400, detail="Invalid file")

    try:
        # Загрузка фото в S3
        file_ext = photo.filename.split('.')[-1].lower()
        file_name = f"{uuid.uuid4()}.{file_ext}"
        file_content = await photo.read()
        
        url = f"{BEGET_S3_ENDPOINT}/{BEGET_S3_BUCKET_NAME}/{file_name}"
        
        now = datetime.utcnow()
        timestamp = now.strftime('%Y%m%dT%H%M%SZ')
        
        headers = {
            'Content-Type': photo.content_type,
            'x-amz-date': timestamp,
            'x-amz-acl': 'public-read',
            'Content-Length': str(len(file_content))
        }
        
        auth = AWS4Auth(
            BEGET_S3_ACCESS_KEY,
            BEGET_S3_SECRET_KEY,
            'ru-1',
            's3'
        )
        
        response = requests.put(
            url,
            data=file_content,
            headers=headers,
            auth=auth
        )
        
        if response.status_code != 200:
            logger.error(f"S3 upload failed: {response.status_code} - {response.text}")
            raise HTTPException(500, detail=f"S3 upload failed: {response.text}")

        photo_url = f"{BEGET_S3_ENDPOINT}/{BEGET_S3_BUCKET_NAME}/{file_name}"

        # Сохранение в БД
        with db.cursor() as cur:
            cur.execute("""
                INSERT INTO posts (
                    photo_url,
                    description,
                    user_id,
                    created_at,
                    likes_count,
                    comments_count,
                    tags,
                    altitude,
                    latitude
                ) VALUES (
                    %s, %s, %s, NOW(), 0, 0, %s, %s, %s
                )
                RETURNING id, created_at
            """, (
                photo_url,
                description,
                current_user["id"],
                tags,
                altitude,
                latitude
            ))
            new_post = cur.fetchone()
            db.commit()

        return {"status": "success", "url": photo_url, "post_id": new_post["id"]}

    except Exception as e:
        logger.error(f"Upload error: {str(e)}", exc_info=True)
        raise HTTPException(500, detail="File upload failed")

@app.get("/tape/posts", response_model=list[PostResponse])
async def get_tape_posts(
    db=Depends(get_db),
    current_user: Optional[dict] = Depends(get_current_user_optional)
):
    try:
        with db.cursor() as cur:
            query = """
                SELECT 
                    p.id,
                    p.photo_url,
                    p.description,
                    p.created_at,
                    p.likes_count,
                    p.comments_count,
                    p.latitude,
                    p.altitude,
                    u.username,
                    COALESCE(u.avatar_url, '/static/default_avatar.jpg') as user_avatar
            """
            
            if current_user:
                query += """,
                    EXISTS(
                        SELECT 1 FROM likes l 
                        WHERE l.post_id = p.id AND l.user_id = %s
                    ) as is_liked
                """
                params = (current_user["id"],)
            else:
                query += ", FALSE as is_liked"
                params = ()
            
            query += """
                FROM posts p
                JOIN users u ON p.user_id = u.id
                ORDER BY p.created_at DESC
                LIMIT 20
            """
            
            cur.execute(query, params)
            posts = cur.fetchall()
            
            for post in posts:
                if post['latitude'] is not None:
                    post['latitude'] = float(post['latitude'])
                if post['altitude'] is not None:
                    post['altitude'] = float(post['altitude'])
                post['is_liked'] = bool(post.get('is_liked', False))
            
            return posts
            
    except Exception as e:
        logger.error(f"Error in get_tape_posts: {str(e)}", exc_info=True)
        raise HTTPException(500, detail="Ошибка при загрузке ленты")

@app.get("/posts/{post_id}", response_model=PostResponse)
async def get_single_post(
    post_id: int,
    db=Depends(get_db),
    current_user: Optional[dict] = Depends(get_current_user_optional)
):
    with db.cursor() as cur:
        query = """
            SELECT 
                p.id,
                p.photo_url,
                p.description,
                p.created_at,
                p.likes_count,
                p.comments_count,
                p.latitude,
                p.altitude,
                u.username,
                COALESCE(u.avatar_url, '/static/default_avatar.jpg') as user_avatar
        """
        
        if current_user:
            query += """,
                EXISTS(
                    SELECT 1 FROM likes l 
                    WHERE l.post_id = p.id AND l.user_id = %s
                ) as is_liked
            """
            params = (post_id, current_user["id"])
        else:
            query += ", FALSE as is_liked"
            params = (post_id,)
        
        query += """
            FROM posts p
            JOIN users u ON p.user_id = u.id
            WHERE p.id = %s
        """
        
        cur.execute(query, params)
        post = cur.fetchone()
        
        if not post:
            raise HTTPException(404, "Post not found")
        
        if post['latitude'] is not None:
            post['latitude'] = float(post['latitude'])
        if post['altitude'] is not None:
            post['altitude'] = float(post['altitude'])
        post['is_liked'] = bool(post.get('is_liked', False))
        
        return post

# Роуты комментариев
@app.post("/posts/{post_id}/comments", response_model=CommentResponse)
async def create_comment(
    post_id: int,
    comment: CommentCreate,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    with db.cursor() as cur:
        # Проверяем существование поста
        cur.execute("SELECT id FROM posts WHERE id = %s", (post_id,))
        if not cur.fetchone():
            raise HTTPException(404, "Post not found")
        
        # Создаем комментарий
        cur.execute("""
            INSERT INTO comments (post_id, user_id, content)
            VALUES (%s, %s, %s)
            RETURNING id, post_id, user_id, content, created_at, updated_at
        """, (post_id, current_user["id"], comment.content))
        new_comment = cur.fetchone()
        
        # Обновляем счетчик комментариев
        cur.execute("""
            UPDATE posts 
            SET comments_count = comments_count + 1 
            WHERE id = %s
        """, (post_id,))
        
        # Получаем данные пользователя
        cur.execute("""
            SELECT username, COALESCE(avatar_url, '/static/default_avatar.jpg') as avatar
            FROM users WHERE id = %s
        """, (current_user["id"],))
        user_data = cur.fetchone()
        
        db.commit()
        
        return {
            **new_comment,
            "username": user_data["username"],
            "avatar": user_data["avatar"]
        }

@app.get("/posts/{post_id}/comments", response_model=list[CommentResponse])
async def get_comments(
    post_id: int,
    skip: int = 0,
    limit: int = 50,
    db=Depends(get_db)
):
    with db.cursor() as cur:
        # Проверяем существование поста
        cur.execute("SELECT id FROM posts WHERE id = %s", (post_id,))
        if not cur.fetchone():
            raise HTTPException(404, "Post not found")
        
        # Получаем комментарии с информацией о пользователях
        cur.execute("""
            SELECT 
                c.id, c.post_id, c.user_id, c.content, 
                c.created_at, c.updated_at,
                u.username,
                COALESCE(u.avatar_url, '/static/default_avatar.jpg') as avatar
            FROM comments c
            JOIN users u ON c.user_id = u.id
            WHERE c.post_id = %s
            ORDER BY c.created_at DESC
            LIMIT %s OFFSET %s
        """, (post_id, limit, skip))
        
        return cur.fetchall()

@app.delete("/comments/{comment_id}")
async def delete_comment(
    comment_id: int,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    with db.cursor() as cur:
        # Проверяем существование комментария и права пользователя
        cur.execute("""
            SELECT id, user_id, post_id FROM comments WHERE id = %s
        """, (comment_id,))
        comment = cur.fetchone()
        
        if not comment:
            raise HTTPException(404, "Comment not found")
        
        if comment["user_id"] != current_user["id"]:
            raise HTTPException(403, "You can delete only your own comments")
        
        # Удаляем комментарий
        cur.execute("DELETE FROM comments WHERE id = %s RETURNING id", (comment_id,))
        deleted_comment = cur.fetchone()
        
        # Обновляем счетчик комментариев в посте
        cur.execute("""
            UPDATE posts 
            SET comments_count = comments_count - 1 
            WHERE id = %s
        """, (comment["post_id"],))
        
        db.commit()
        
        if not deleted_comment:
            raise HTTPException(500, "Failed to delete comment")
        
        return {"status": "ok", "comment_id": comment_id}

# Роуты лайков
@app.post("/posts/{post_id}/like")
async def like_post(
    post_id: int,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    with db.cursor() as cur:
        # Проверяем существование поста
        cur.execute("SELECT id FROM posts WHERE id = %s", (post_id,))
        if not cur.fetchone():
            raise HTTPException(404, "Post not found")
        
        # Проверяем, не лайкал ли уже пользователь
        cur.execute("""
            SELECT id FROM likes 
            WHERE user_id = %s AND post_id = %s
        """, (current_user["id"], post_id))
        
        if cur.fetchone():
            raise HTTPException(400, "You already liked this post")
        
        # Добавляем лайк
        cur.execute("""
            INSERT INTO likes (user_id, post_id)
            VALUES (%s, %s)
        """, (current_user["id"], post_id))
        
        # Обновляем счетчик лайков
        cur.execute("""
            UPDATE posts 
            SET likes_count = likes_count + 1 
            WHERE id = %s
            RETURNING likes_count
        """, (post_id,))
        
        updated_count = cur.fetchone()["likes_count"]
        db.commit()
        
        return {"status": "success", "likes_count": updated_count}

@app.post("/posts/{post_id}/unlike")
async def unlike_post(
    post_id: int,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    with db.cursor() as cur:
        # Проверяем существование поста
        cur.execute("SELECT id FROM posts WHERE id = %s", (post_id,))
        if not cur.fetchone():
            raise HTTPException(404, "Post not found")
        
        # Удаляем лайк
        cur.execute("""
            DELETE FROM likes 
            WHERE user_id = %s AND post_id = %s
            RETURNING id
        """, (current_user["id"], post_id))
        
        if not cur.fetchone():
            raise HTTPException(400, "You haven't liked this post yet")
        
        # Обновляем счетчик лайков
        cur.execute("""
            UPDATE posts 
            SET likes_count = likes_count - 1 
            WHERE id = %s
            RETURNING likes_count
        """, (post_id,))
        
        updated_count = cur.fetchone()["likes_count"]
        db.commit()
        
        return {"status": "success", "likes_count": updated_count}

# Остальные роуты
@app.get("/")
async def root():
    return {"status": "ok"}

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.exception_handler(500)
async def internal_server_error_handler(request: Request, exc: Exception):
    return PlainTextResponse("Internal Server Error", status_code=500)

@app.exception_handler(404)
async def not_found_handler(request: Request, exc: Exception):
    return FileResponse("static/404.html", status_code=404)

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
    #sdsds