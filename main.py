from fastapi import FastAPI, HTTPException, Depends, status, Request, UploadFile, File, Form
from fastapi.exceptions import RequestValidationError
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, FileResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel, EmailStr, field_validator, AnyUrl, Field
from passlib.context import CryptContext
from jose import JWTError, jwt
from typing import Optional
import psycopg2
from psycopg2.extras import RealDictCursor
import os
from dotenv import load_dotenv
from pathlib import Path
from starlette.middleware.base import BaseHTTPMiddleware
import logging
import boto3
from botocore.exceptions import NoCredentialsError, ClientError
import uuid
import shutil
import requests
from requests_aws4auth import AWS4Auth
from datetime import datetime, timedelta
import hashlib
import base64
from botocore.exceptions import ClientError
from slugify import slugify
from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS
import io


# Настройкиi
load_dotenv()

# Конфигурация с fallback значениями
SECRET_KEY = os.getenv("SECRET_KEY", "fallback-secret-key-for-dev")
DB_URL = os.getenv("AUTH_DATABASE_URL")
BEGET_S3_ENDPOINT = os.getenv("BEGET_S3_ENDPOINT", "")
BEGET_S3_BUCKET_NAME = os.getenv("BEGET_S3_BUCKET_NAME", "")
BEGET_S3_ACCESS_KEY = os.getenv("BEGET_S3_ACCESS_KEY", "")
BEGET_S3_SECRET_KEY = os.getenv("BEGET_S3_SECRET_KEY", "")

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

WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
CITY = "Ekaterinburg"

PHOTO_TIPS: dict[str, dict[str, list[str]]] = {
    "clear": {
        "day": [
            "Яркое солнце 🌞 - используйте бленду объектива и поляризационный фильтр для насыщенных цветов. ISO 100, f/8-f/11.",
            "Резкие тени? Снимайте в режимное время или используйте отражатель. Выдержка 1/250-1/1000.",
            "Контровой свет создаёт красивые блики. Попробуйте f/2.8 для боке. Золотой час в {sunset}!",
            "Солнечно - идеально для пейзажей с глубокой резкостью. Диафрагма f/11-f/16, штатив обязателен.",
            "Попробуйте ч/б фотографию - контраст теней будет выглядеть эффектно. ISO 200-400."
        ],
        "night": [
            "Звёздное небо? Широкоугольник, f/2.8, выдержка 15-30 сек, ISO 1600-3200. Используйте штатив!",
            "Луна освещает пейзаж? Попробуйте выдержку 1/125, f/5.6, ISO 400.",
            "Городские огни ночью - f/8, выдержка 2-5 сек, низкий ISO для минимизации шумов.",
            "Световая живопись! Установите камеру на штатив, выдержка 10-30 сек, рисуйте фонарём."
        ]
    },
    "clouds": {
        "day": [
            "Мягкий свет ☁️ - идеально для портретов. f/2.8-f/5.6, выдержка 1/125-1/250. Попробуйте холодные тона.",
            "Облака как гигантский софтбокс! Отлично для предметки. f/8-f/11, ISO 200-400.",
            "Пасмурно - время для moody-фото. +1EV экспозиции, тёплый баланс белого.",
            "Серая погода? Ищите яркие акценты - зонты, витрины, элементы одежды.",
            "Рассеянный свет - снимите лесные пейзажи с детализацией в тенях. f/8, +0.7EV."
        ],
        "night": [
            "Облачное небо ночью отражает городской свет - попробуйте длинную выдержку 5-10 сек.",
            "Тёмные облака создают драматичный фон для ночной съёмки архитектуры."
        ]
    },
    "rain": {
        "day": [
            "Дождь 🌧 - защитите технику! Используйте зонт или дождевик для камеры. f/5.6, выдержка 1/125.",
            "Капли на стекле - прекрасный объект для макросъёмки. f/2.8, выдержка 1/250, ISO 400.",
            "Мокрые улицы отражают свет - ищите интересные отражения. f/8, выдержка 1/60.",
            "Дождь создаёт мягкий свет - отлично подходит для портретов с эмоциями.",
            "Снимайте через окно с каплями дождя для художественного эффекта."
        ],
        "night": [
            "Ночной дождь - используйте длинную выдержку 1-2 сек для съёмки световых полос от фонарей.",
            "Мокрый асфальт отражает огни города - попробуйте низкий угол съёмки."
        ]
    },
    "snow": {
        "day": [
            "Снег ❄️ - увеличьте экспозицию на +1EV для правильной передачи белого. f/8, ISO 200.",
            "Снежинки крупным планом - используйте макрообъектив. f/2.8, выдержка 1/500.",
            "Зимние пейзажи лучше снимать в голубой час. Штатив обязателен!",
            "Снегопад - попробуйте выдержку 1/125-1/250 для заморозки движения снежинок.",
            "Ищите контрасты: красные предметы на белом снегу смотрятся эффектно."
        ],
        "night": [
            "Снег ночью отражает свет - можно снимать с более низким ISO. Попробуйте 30 сек выдержки.",
            "Новогодние огни в снегу - установите баланс белого на 3000K для тёплых тонов."
        ]
    },
    "fog": {
        "day": [
            "Туман 🌫 - используйте ручную фокусировку. f/5.6-f/8 для глубины.",
            "Силуэты в тумане - экспонируйте по светлым участкам. Контрастность +20 в постобработке.",
            "Туман смягчает фон - идеально для портретов с размытием.",
            "Ищите одинокие объекты в тумане - деревья, фонари, здания.",
            "Туман + лес = готовый кадр в стиле фэнтези. Попробуйте холодные тона."
        ],
        "night": [
            "Туман ночью рассеивает свет - попробуйте снимать световые столбы от фонарей.",
            "Город в тумане - установите баланс белого на 4000K для холодного настроения."
        ]
    },
    "thunderstorm": {
        "day": [
            "Гроза ⚡ - будьте осторожны! Снимайте из укрытия. Длинная выдержка 10-30 сек для молний.",
            "Драматичное небо перед грозой - используйте градиентный ND-фильтр.",
            "После дождя - ищите отражения в лужах. f/8, выдержка 1/125."
        ],
        "night": [
            "Ночная гроза - используйте пульт ДУ и штатив. Выдержка BULB для захвата молний.",
            "Молнии на фоне города - фокус на бесконечность, ISO 400."
        ]
    }
}

CAMERA_SETTINGS_TIPS = {
    "clear": "Совет: При ярком солнце используйте ND-фильтр для длинных выдержек",
    "clouds": "Совет: При облачности можно увеличить ISO до 400-800 без сильных шумов",
    "rain": "Совет: Используйте бленду для защиты линзы от капель дождя",
    "snow": "Совет: Держите запасные батареи в тепле - на холоде они разряжаются быстрее",
    "fog": "Совет: Используйте бленду для уменьшения бликов от влажного воздуха",
    "thunderstorm": "Совет: Защитите камеру дождевым чехлом или полиэтиленовым пакетом",
    "default": "Совет: Экспериментируйте с настройками и пробуйте новые ракурсы"
}



# Настройка логгирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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

# Проверка доступности S3
if not all([BEGET_S3_ENDPOINT, BEGET_S3_BUCKET_NAME, BEGET_S3_ACCESS_KEY, BEGET_S3_SECRET_KEY]):
    logger.error("S3 credentials not configured")
    raise HTTPException(500, "S3 storage not configured")



# Модели

async def upload_to_s3(file: UploadFile, folder: str) -> str:
    try:
        file_content = await file.read()
        file_ext = file.filename.split('.')[-1].lower()
        file_name = f"{folder}/{uuid.uuid4()}.{file_ext}"
        
        # Используем put_object вместо upload_fileobj
        s3.put_object(
            Bucket=BEGET_S3_BUCKET_NAME,
            Key=file_name,
            Body=file_content,
            ContentType=file.content_type,
            ACL='public-read'
        )
        
        return f"{BEGET_S3_ENDPOINT}/{BEGET_S3_BUCKET_NAME}/{file_name}"
    except Exception as e:
        logger.error(f"Upload error: {str(e)}", exc_info=True)
        raise HTTPException(500, f"Failed to upload file: {str(e)}")
    finally:
        await file.seek(0)

# Модель ответа API
class Post(BaseModel):
    id: int
    latitude: float
    altitude: float
    likes_count: int
    photo_url: str
    camera_model: Optional[str] = None
    camera_settings: Optional[dict] = None



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

# Подключение к БД (new)
def get_db():
    conn = psycopg2.connect(
        dbname="auth_db_17at_90uu",
        user="auth_db_17at_user",
        password="rOK6TE8lX6zIisiF2E2siOmbGPnpUGxI",
        host="dpg-d0qvflje5dus739v4q50-a.oregon-postgres.render.com",
        port="5432",
        cursor_factory=RealDictCursor,
        sslmode="require"  
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
        
        # Обработка GPS данных
        if 'GPSInfo' in exif_data:
            gps_info = {
                GPSTAGS.get(tag, tag): value
                for tag, value in exif_data['GPSInfo'].items()
            }
            exif_data['GPSInfo'] = gps_info

        # Форматирование полезных данных
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
                   COALESCE(av  atar_url, '/default-avatar.jpg') as avatar
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

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    for error in errors:
        if error["type"] == "value_error.email":
            return JSONResponse(
                status_code=400,
                content={"detail": "Некорректный формат email"},
            )
    return JSONResponse(
        status_code=400,
        content={"detail": "Ошибка валидации данных"},
    )
    
@app.put("/users/{username}", response_model=User)
async def update_user_profile(
    username: str,
    avatar: Optional[UploadFile] = File(None),
    bio: str = Form(default=""),
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    if current_user["username"] != username:
        raise HTTPException(403, "You can update only your own profile")
    
    avatar_url = current_user["avatar"]

    if avatar:
        
        if avatar.content_type not in ALLOWED_TYPES:
            raise HTTPException(400, "Unsupported file type. Only JPEG, PNG and WebP are allowed")
        try:
            avatar_url = await upload_to_s3(avatar, "avatars")
        except Exception as e:
            logger.error(f"Avatar upload error: {str(e)}")
            raise HTTPException(500, "Failed to upload avatar")

    with db.cursor() as cur:
        cur.execute("""
            UPDATE users 
            SET bio = %s, avatar_url = %s
            WHERE username = %s
            RETURNING id, username, email, bio, avatar_url
        """, (bio, avatar_url, username))
        updated_user = cur.fetchone()
        db.commit()
        
        if not updated_user:
            raise HTTPException(404, "User not found")
        
        return {
            "id": updated_user["id"],
            "username": updated_user["username"],
            "email": updated_user["email"],
            "profile": {
                "bio": updated_user["bio"],
                "avatar": updated_user["avatar_url"]
            }
        }
        
        
        
@app.get("/weather-message")
async def get_weather_message():
    weather_data = await fetch_weather()
    if not weather_data:
        return {"message": "Не удалось получить данные о погоде."}
    
    message = generate_photographer_message(weather_data)
    return {
        "message": message,
        "weather_data": weather_data,
        "camera_tip": get_camera_tip(weather_data)
    }

async def fetch_weather():
    try:
        url = f"http://api.openweathermap.org/data/2.5/weather?q={CITY}&appid={WEATHER_API_KEY}&units=metric&lang=ru"
        response = requests.get(url)
        data = response.json()
        
        if response.status_code != 200:
            print("Ошибка API погоды:", data.get("message", "Unknown error"))
            return None
        
        return {
            "temp": data["main"]["temp"],
            "condition": data["weather"][0]["description"],
            "sunset": datetime.fromtimestamp(data["sys"]["sunset"]).strftime("%H:%M"),
            "sunrise": datetime.fromtimestamp(data["sys"]["sunrise"]).strftime("%H:%M"),
            "humidity": data["main"]["humidity"],
            "clouds": data["clouds"]["all"],
            "wind": data["wind"]["speed"],
            "time": datetime.now().strftime("%H:%M")
        }
    except Exception as e:
        print("Ошибка при запросе погоды:", e)
        return None

def get_time_of_day(sunset: str, sunrise: str) -> str:
    now = datetime.now().strftime("%H:%M")
    return "night" if now > sunset or now < sunrise else "day"

def get_weather_key(condition: str) -> str:
    condition = condition.lower()
    if "ясно" in condition or "clear" in condition:
        return "clear"
    elif "облачно" in condition or "clouds" in condition:
        return "clouds"
    elif "дождь" in condition or "rain" in condition:
        return "rain"
    elif "снег" in condition or "snow" in condition:
        return "snow"
    elif "туман" in condition or "fog" in condition:
        return "fog"
    elif "гроза" in condition or "thunderstorm" in condition:
        return "thunderstorm"
    return "default"

def get_camera_tip(weather: dict) -> str:
    weather_key = get_weather_key(weather["condition"])
    return CAMERA_SETTINGS_TIPS.get(weather_key, CAMERA_SETTINGS_TIPS["default"])

def generate_photographer_message(weather: dict) -> str:
    weather_key = get_weather_key(weather["condition"])
    time_of_day = get_time_of_day(weather["sunset"], weather["sunrise"])
    
    if weather_key not in PHOTO_TIPS or time_of_day not in PHOTO_TIPS[weather_key]:
        weather_key = "default"
        time_of_day = "day"
    
    tips = PHOTO_TIPS.get(weather_key, {}).get(time_of_day, 
             PHOTO_TIPS["clear"]["day"])  # fallback
    
    chosen_tip = random.choice(tips)
    return chosen_tip.format(
        sunset=weather["sunset"],
        sunrise=weather["sunrise"],
        temp=weather["temp"]
    )
    
    
    

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



@app.get("/points", response_model=list[Post])
def get_points(limit: int = 10):
    try:
        # Создаем новое подключение вместо использования генератора
        conn = psycopg2.connect(
            dbname="auth_db_17at",
            user="auth_db_17at_user",
            password="eGFSjcl2O29bWJBCAcRdh4MIzjNFojNZ",
            host="dpg-d05lfk2li9vc738u4vb0-a.oregon-postgres.render.com",
            port="5432",
            cursor_factory=RealDictCursor
        )
        
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT id, latitude, altitude, 
                       likes_count, photo_url
                FROM posts 
                ORDER BY likes_count DESC 
                LIMIT %s
            """, (limit,))
            
            posts = cursor.fetchall()
        
        conn.close()
        
        # Преобразуем координаты в float
        for post in posts:
            post["latitude"] = float(post["latitude"])
            post["altitude"] = float(post["altitude"])
        
        return posts
    
    except Exception as e:
        raise HTTPException(500, detail=f"Ошибка базы данных: {str(e)}")
    

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
@app.post("/posts")
async def create_post(
    photo: UploadFile = File(...),
    description: str = Form(default=""),
    altitude: str = Form(...),
    latitude: str = Form(...),
    camera_model: str = Form(...),
    exposure: str = Form(default=""),
    aperture: str = Form(default=""),
    iso: str = Form(default=""),
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    if not photo.filename or not photo.content_type:
        raise HTTPException(400, detail="Invalid file")
    
    if photo.content_type not in ALLOWED_TYPES:
        raise HTTPException(400, "Unsupported file type. Only JPEG, PNG and WebP are allowed")
    
    try:
        # Извлекаем EXIF данные
        file_content = await photo.read()
        exif_data = extract_exif_data(file_content)
        await photo.seek(0)

        # Загружаем фото
        photo_url = await upload_to_s3(photo, "posts")

        # Формируем настройки камеры
        camera_settings = {
            "model": camera_model,
            "exposure": exposure or exif_data.get("exposure_time", ""),
            "aperture": aperture or exif_data.get("f_number", ""),
            "iso": iso or exif_data.get("iso", ""),
            "focal_length": exif_data.get("focal_length", ""),
            "lens": exif_data.get("lens_model", "")
        }

        # Сохраняем в базу данных
        with db.cursor() as cur:
            cur.execute("""
                INSERT INTO posts (
                    photo_url, description, user_id, created_at,
                    likes_count, comments_count, altitude, latitude,
                    camera_model, camera_settings
                ) VALUES (
                    %s, %s, %s, NOW(), 0, 0, %s, %s, %s, %s
                )
                RETURNING id, created_at
            """, (
                photo_url, 
                description, 
                current_user["id"],
                altitude, 
                latitude, 
                camera_model,
                json.dumps(camera_settings),
            ))
            new_post = cur.fetchone()
            db.commit()

        return {
            "status": "success",
            "url": photo_url,
            "post_id": new_post["id"],
            "camera_info": camera_settings
        }

    except Exception as e:
        logger.error(f"Post creation error: {str(e)}", exc_info=True)
        raise HTTPException(500, detail=f"Failed to create post: {str(e)}")

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

@app.get("/posts/{post_id}/{optional_slug}")
async def get_post(
    post_id: int,
    optional_slug: Optional[str] = None,
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

        # Редирект на URL с slug, если его нет
        if not optional_slug:
            slug = slugify(post["description"]) if post["description"] else f"post-{post_id}"
            return RedirectResponse(f"/posts/{post_id}/{slug}")

        return post

# Эндпоинт для лайка поста
@app.post("/posts/{post_id}/like")
async def like_post(
    post_id: int,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    try:
        with db.cursor() as cur:
            # Проверяем, существует ли пост
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
    except Exception as e:
        db.rollback()
        raise HTTPException(500, detail=f"Database error: {str(e)}")

# Эндпоинт для удаления лайка
@app.post("/posts/{post_id}/unlike")
async def unlike_post(
    post_id: int,
    db=Depends(get_db),
    current_user=Depends(get_current_user)
):
    try:
        with db.cursor() as cur:
            # Проверяем, существует ли пост
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
    except Exception as e:
        db.rollback()
        raise HTTPException(500, detail=f"Database error: {str(e)}")

# Эндпоинт для получения ленты постов
@app.get("/tape/posts")
async def get_tape_posts(db=Depends(get_db)):
    try:
        with db.cursor() as cur:
            # Базовый запрос для всех пользователей (авторизованных и нет)
            cur.execute("""
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
                    u.avatar_url as user_avatar,
                    FALSE as is_liked
                FROM posts p
                JOIN users u ON p.user_id = u.id
                ORDER BY p.created_at DESC
                LIMIT 20
            """)
            
            posts = cur.fetchall()
            
            # Преобразование типов данных
            for post in posts:
                post['latitude'] = float(post.get('latitude', 0))
                post['altitude'] = float(post.get('altitude', 0))
                post['is_liked'] = bool(post.get('is_liked', False))
            
            return posts
            
    except Exception as e:
        logger.error(f"Error in get_tape_posts: {str(e)}", exc_info=True)
        raise HTTPException(500, detail="Ошибка при загрузке ленты")

@app.get("/post/{post_id}")
async def get_single_post_page(request: Request, post_id: int, db=Depends(get_db)):
    # Проверяем существование поста
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
    
    return templates.TemplateResponse("post.html", {
        "request": request,
        "post": post
    })

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

@app.get("/check-s3")
async def check_s3():
    try:
        # Пробуем просто получить список бакетов
        response = s3.list_buckets()
        return {
            "status": "success",
            "buckets": [b['Name'] for b in response['Buckets']],
            "bucket_exists": BEGET_S3_BUCKET_NAME in [b['Name'] for b in response['Buckets']]
        }
    except Exception as e:
        logger.error(f"S3 connection error: {str(e)}")
        return {
            "status": "error",
            "error": str(e),
            "details": {
                "endpoint": BEGET_S3_ENDPOINT,
                "bucket": BEGET_S3_BUCKET_NAME,
                "region": "ru-1"
            }
        }

app.add_middleware(NotFoundMiddleware)

#исключение ошибок
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