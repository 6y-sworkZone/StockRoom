from pydantic import BaseModel, EmailStr
from datetime import datetime, date
from typing import Optional, List
from models import OperationType, UserFamilyRole


class Token(BaseModel):
    access_token: str
    token_type: str


class TokenData(BaseModel):
    username: Optional[str] = None


class UserBase(BaseModel):
    username: str
    email: EmailStr


class UserCreate(UserBase):
    password: str


class UserResponse(UserBase):
    id: int
    created_at: datetime
    current_family_id: Optional[int] = None

    class Config:
        from_attributes = True


class FamilyBase(BaseModel):
    name: str
    description: Optional[str] = None


class FamilyCreate(FamilyBase):
    pass


class FamilyResponse(FamilyBase):
    id: int
    created_at: datetime
    created_by: int

    class Config:
        from_attributes = True


class FamilyMemberResponse(BaseModel):
    id: int
    user_id: int
    family_id: int
    role: UserFamilyRole
    joined_at: datetime
    user: UserResponse

    class Config:
        from_attributes = True


class InvitationCreate(BaseModel):
    family_id: int


class InvitationResponse(BaseModel):
    id: int
    family_id: int
    code: str
    created_at: datetime
    expires_at: datetime
    used: bool

    class Config:
        from_attributes = True


class InvitationUse(BaseModel):
    code: str


class LocationBase(BaseModel):
    name: str
    parent_id: Optional[int] = None


class LocationCreate(LocationBase):
    pass


class LocationUpdate(BaseModel):
    name: Optional[str] = None
    sort_order: Optional[int] = None


class LocationResponse(BaseModel):
    id: int
    name: str
    level: int
    parent_id: Optional[int]
    sort_order: int
    children: List["LocationResponse"] = []

    class Config:
        from_attributes = True


class CategoryWarningBase(BaseModel):
    category: str
    warning_days: int


class CategoryWarningCreate(CategoryWarningBase):
    pass


class CategoryWarningResponse(CategoryWarningBase):
    id: int

    class Config:
        from_attributes = True


class ItemBase(BaseModel):
    name: str
    category: str
    quantity: float = 0
    unit: str
    location_id: Optional[int] = None
    purchase_date: Optional[date] = None
    expiry_date: Optional[date] = None
    price: Optional[float] = None
    remarks: Optional[str] = None
    spec: Optional[str] = None
    low_stock_threshold: float = 0


class ItemCreate(ItemBase):
    pass


class ItemUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None
    location_id: Optional[int] = None
    purchase_date: Optional[date] = None
    expiry_date: Optional[date] = None
    price: Optional[float] = None
    remarks: Optional[str] = None
    spec: Optional[str] = None
    low_stock_threshold: Optional[float] = None


class ItemResponse(ItemBase):
    id: int
    family_id: int
    created_at: datetime
    updated_at: Optional[datetime]
    created_by: int
    photo_path: Optional[str] = None
    is_deleted: bool
    location: Optional[LocationResponse] = None

    class Config:
        from_attributes = True


class InventoryRecordBase(BaseModel):
    item_id: int
    operation_type: OperationType
    quantity_change: float
    remarks: Optional[str] = None


class InventoryRecordCreate(InventoryRecordBase):
    pass


class InventoryRecordResponse(BaseModel):
    id: int
    item_id: int
    operation_type: OperationType
    quantity_before: float
    quantity_after: float
    quantity_change: float
    operator_id: int
    remarks: Optional[str]
    is_reverse: bool
    created_at: datetime
    operator: UserResponse

    class Config:
        from_attributes = True


class ReverseRecordCreate(BaseModel):
    remarks: Optional[str] = None


class ShoppingListBase(BaseModel):
    item_name: str
    suggested_quantity: Optional[float] = None
    actual_quantity: Optional[float] = None
    unit: Optional[str] = None


class ShoppingListCreate(ShoppingListBase):
    pass


class ShoppingListResponse(ShoppingListBase):
    id: int
    family_id: int
    is_auto_generated: bool
    is_purchased: bool
    purchased_at: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True


class ShoppingListPurchase(BaseModel):
    actual_quantity: float
    unit: str
    location_id: Optional[int] = None
    price: Optional[float] = None


class StatisticsResponse(BaseModel):
    category_pie: dict
    monthly_trend: dict
    location_bar: dict
    low_stock: List[ItemResponse]


class ExpiryWarningResponse(BaseModel):
    expired: List[ItemResponse]
    expiring_soon: List[ItemResponse]


LocationResponse.model_rebuild()
