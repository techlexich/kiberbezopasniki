from pydantic import BaseModel

class UserProfile(BaseModel):
    bio: str = ""
    avatar: str = "/default-avatar.jpg"

class User(BaseModel):
    id: int
    username: str
    email: str
    profile: UserProfile = UserProfile()

class UserStats(BaseModel):
    posts: int = 0
    followers: int = 0
    following: int = 0

class UserProfile(BaseModel):
    bio: str = ""
    avatar: str = "/default-avatar.jpg"
    stats: UserStats = UserStats()