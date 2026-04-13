from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import User, UserRole


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """세션에서 로그인된 사용자를 가져온다."""
    user_data = request.session.get("user")
    if not user_data:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")

    user = db.query(User).filter(User.email == user_data["email"]).first()
    if not user:
        raise HTTPException(status_code=401, detail="사용자를 찾을 수 없습니다.")
    return user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """admin 권한이 필요한 엔드포인트에 사용."""
    if current_user.role != UserRole.admin:
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")
    return current_user


def require_user(current_user: User = Depends(get_current_user)) -> User:
    """로그인만 되어 있으면 접근 가능한 엔드포인트에 사용."""
    return current_user
