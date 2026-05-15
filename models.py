from sqlalchemy import Column, Integer, String, Float, DateTime, Text, ForeignKey, Boolean, Enum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base
import enum


class OperationType(str, enum.Enum):
    IN = "入库"
    OUT = "出库"
    CONSUME = "消耗"
    DISCARD = "丢弃"
    TRANSFER = "转移"
    ADJUST = "调整"
    CHANGE = "变更数量"


class UserFamilyRole(str, enum.Enum):
    OWNER = "所有者"
    ADMIN = "管理员"
    MEMBER = "成员"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    email = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    current_family_id = Column(Integer, ForeignKey("families.id"), nullable=True)

    families = relationship("FamilyMember", back_populates="user", cascade="all, delete-orphan")
    items = relationship("Item", back_populates="creator")
    inventory_records = relationship("InventoryRecord", back_populates="operator")


class Family(Base):
    __tablename__ = "families"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    created_by = Column(Integer, ForeignKey("users.id"))

    members = relationship("FamilyMember", back_populates="family", cascade="all, delete-orphan")
    invitations = relationship("FamilyInvitation", back_populates="family", cascade="all, delete-orphan")
    locations = relationship("Location", back_populates="family", cascade="all, delete-orphan")
    items = relationship("Item", back_populates="family", cascade="all, delete-orphan")
    category_warnings = relationship("CategoryWarning", back_populates="family", cascade="all, delete-orphan")
    shopping_lists = relationship("ShoppingList", back_populates="family", cascade="all, delete-orphan")


class FamilyMember(Base):
    __tablename__ = "family_members"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    family_id = Column(Integer, ForeignKey("families.id"))
    role = Column(Enum(UserFamilyRole), default=UserFamilyRole.MEMBER)
    joined_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="families")
    family = relationship("Family", back_populates="members")


class FamilyInvitation(Base):
    __tablename__ = "family_invitations"

    id = Column(Integer, primary_key=True, index=True)
    family_id = Column(Integer, ForeignKey("families.id"))
    code = Column(String, unique=True, index=True)
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True))
    used = Column(Boolean, default=False)
    used_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    used_at = Column(DateTime(timezone=True), nullable=True)

    family = relationship("Family", back_populates="invitations")


class Location(Base):
    __tablename__ = "locations"

    id = Column(Integer, primary_key=True, index=True)
    family_id = Column(Integer, ForeignKey("families.id"))
    name = Column(String)
    level = Column(Integer)
    parent_id = Column(Integer, ForeignKey("locations.id"), nullable=True)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    family = relationship("Family", back_populates="locations")
    parent = relationship("Location", remote_side=[id], back_populates="children")
    children = relationship("Location", back_populates="parent", cascade="all, delete-orphan")
    items = relationship("Item", back_populates="location")


class CategoryWarning(Base):
    __tablename__ = "category_warnings"

    id = Column(Integer, primary_key=True, index=True)
    family_id = Column(Integer, ForeignKey("families.id"))
    category = Column(String)
    warning_days = Column(Integer, default=7)

    family = relationship("Family", back_populates="category_warnings")


class Item(Base):
    __tablename__ = "items"

    id = Column(Integer, primary_key=True, index=True)
    family_id = Column(Integer, ForeignKey("families.id"))
    name = Column(String, index=True)
    category = Column(String, index=True)
    quantity = Column(Float, default=0)
    unit = Column(String)
    location_id = Column(Integer, ForeignKey("locations.id"), nullable=True)
    purchase_date = Column(DateTime(timezone=True), nullable=True)
    expiry_date = Column(DateTime(timezone=True), nullable=True)
    price = Column(Float, nullable=True)
    remarks = Column(Text, nullable=True)
    photo_path = Column(String, nullable=True)
    spec = Column(String, nullable=True)
    low_stock_threshold = Column(Float, default=0)
    is_deleted = Column(Boolean, default=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    created_by = Column(Integer, ForeignKey("users.id"))

    family = relationship("Family", back_populates="items")
    location = relationship("Location", back_populates="items")
    creator = relationship("User", back_populates="items")
    inventory_records = relationship("InventoryRecord", back_populates="item")


class InventoryRecord(Base):
    __tablename__ = "inventory_records"

    id = Column(Integer, primary_key=True, index=True)
    item_id = Column(Integer, ForeignKey("items.id"))
    operation_type = Column(Enum(OperationType))
    quantity_before = Column(Float)
    quantity_after = Column(Float)
    quantity_change = Column(Float)
    operator_id = Column(Integer, ForeignKey("users.id"))
    remarks = Column(Text, nullable=True)
    is_reverse = Column(Boolean, default=False)
    reversed_record_id = Column(Integer, ForeignKey("inventory_records.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    item = relationship("Item", back_populates="inventory_records")
    operator = relationship("User", back_populates="inventory_records")


class ShoppingList(Base):
    __tablename__ = "shopping_lists"

    id = Column(Integer, primary_key=True, index=True)
    family_id = Column(Integer, ForeignKey("families.id"))
    item_name = Column(String)
    suggested_quantity = Column(Float, nullable=True)
    actual_quantity = Column(Float, nullable=True)
    unit = Column(String, nullable=True)
    is_auto_generated = Column(Boolean, default=False)
    is_purchased = Column(Boolean, default=False)
    purchased_at = Column(DateTime(timezone=True), nullable=True)
    purchased_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    family = relationship("Family", back_populates="shopping_lists")
