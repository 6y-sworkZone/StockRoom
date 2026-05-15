from datetime import datetime, timedelta
from typing import List, Optional
import uuid
import os
import pandas as pd
import aiofiles
import httpx
from io import StringIO
from fastapi import FastAPI, Depends, HTTPException, status, UploadFile, File
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_
from database import get_db, engine, Base
from config import get_settings
from auth import get_current_active_user, create_access_token, authenticate_user, get_password_hash
from models import (
    User, Family, FamilyMember, FamilyInvitation, Location, Item,
    InventoryRecord, CategoryWarning, ShoppingList, OperationType, UserFamilyRole
)
from schemas import (
    Token, UserCreate, UserResponse, FamilyCreate, FamilyResponse,
    FamilyMemberResponse, InvitationCreate, InvitationResponse, InvitationUse,
    LocationCreate, LocationUpdate, LocationResponse, ItemCreate, ItemUpdate, ItemResponse,
    InventoryRecordCreate, InventoryRecordResponse, ReverseRecordCreate,
    ShoppingListCreate, ShoppingListResponse, ShoppingListPurchase,
    StatisticsResponse, ExpiryWarningResponse, CategoryWarningCreate, CategoryWarningResponse
)

settings = get_settings()

app = FastAPI(title="家庭物品库存管理系统")

os.makedirs("uploads", exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def send_expiry_webhook(expired_items: List[Item], expiring_items: List[Item]):
    if not settings.WEBHOOK_URL:
        return
    
    try:
        payload = {
            "event": "expiry_warning",
            "timestamp": datetime.utcnow().isoformat(),
            "expired_count": len(expired_items),
            "expiring_count": len(expiring_items),
            "expired_items": [
                {
                    "id": item.id,
                    "name": item.name,
                    "category": item.category,
                    "expiry_date": item.expiry_date.isoformat() if item.expiry_date else None,
                    "quantity": item.quantity,
                    "unit": item.unit
                } for item in expired_items
            ],
            "expiring_items": [
                {
                    "id": item.id,
                    "name": item.name,
                    "category": item.category,
                    "expiry_date": item.expiry_date.isoformat() if item.expiry_date else None,
                    "quantity": item.quantity,
                    "unit": item.unit
                } for item in expiring_items
            ]
        }
        
        async with httpx.AsyncClient() as client:
            await client.post(settings.WEBHOOK_URL, json=payload, timeout=10)
    except Exception as e:
        print(f"Webhook 推送失败: {e}")


@app.post("/token", response_model=Token)
async def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db)
):
    user = await authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token = create_access_token(data={"sub": user.username})
    return {"access_token": access_token, "token_type": "bearer"}


@app.post("/users/", response_model=UserResponse)
async def create_user(user: UserCreate, db: AsyncSession = Depends(get_db)):
    db_user = await db.execute(select(User).where(User.username == user.username))
    if db_user.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="用户名已存在")
    db_user = await db.execute(select(User).where(User.email == user.email))
    if db_user.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="邮箱已存在")
    
    hashed_password = get_password_hash(user.password)
    db_user = User(username=user.username, email=user.email, hashed_password=hashed_password)
    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    return db_user


@app.get("/users/me/", response_model=UserResponse)
async def read_users_me(current_user: User = Depends(get_current_active_user)):
    return current_user


@app.get("/users/me/families/", response_model=List[FamilyResponse])
async def get_user_families(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(Family)
        .join(FamilyMember)
        .where(FamilyMember.user_id == current_user.id)
    )
    return result.scalars().all()


@app.post("/switch-family/{family_id}")
async def switch_family(
    family_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    membership = await db.execute(
        select(FamilyMember)
        .where(and_(FamilyMember.user_id == current_user.id, FamilyMember.family_id == family_id))
    )
    if not membership.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="您不是该家庭的成员")
    
    current_user.current_family_id = family_id
    await db.commit()
    return {"message": "已切换家庭"}


@app.post("/families/", response_model=FamilyResponse)
async def create_family(
    family: FamilyCreate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    db_family = Family(
        name=family.name,
        description=family.description,
        created_by=current_user.id
    )
    db.add(db_family)
    await db.commit()
    await db.refresh(db_family)
    
    member = FamilyMember(
        user_id=current_user.id,
        family_id=db_family.id,
        role=UserFamilyRole.OWNER
    )
    db.add(member)
    await db.commit()
    
    if not current_user.current_family_id:
        current_user.current_family_id = db_family.id
        await db.commit()
    
    return db_family


@app.get("/families/{family_id}/members/", response_model=List[FamilyMemberResponse])
async def get_family_members(
    family_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    membership = await db.execute(
        select(FamilyMember)
        .where(and_(FamilyMember.user_id == current_user.id, FamilyMember.family_id == family_id))
    )
    if not membership.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="您不是该家庭的成员")
    
    result = await db.execute(
        select(FamilyMember)
        .where(FamilyMember.family_id == family_id)
    )
    return result.scalars().all()


@app.post("/invitations/", response_model=InvitationResponse)
async def create_invitation(
    invitation: InvitationCreate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    membership = await db.execute(
        select(FamilyMember)
        .where(and_(
            FamilyMember.user_id == current_user.id,
            FamilyMember.family_id == invitation.family_id,
            FamilyMember.role.in_([UserFamilyRole.OWNER, UserFamilyRole.ADMIN])
        ))
    )
    if not membership.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="您没有权限创建邀请")
    
    code = str(uuid.uuid4())[:8].upper()
    db_invitation = FamilyInvitation(
        family_id=invitation.family_id,
        code=code,
        created_by=current_user.id,
        expires_at=datetime.utcnow() + timedelta(days=7)
    )
    db.add(db_invitation)
    await db.commit()
    await db.refresh(db_invitation)
    return db_invitation


@app.post("/invitations/use/")
async def use_invitation(
    invitation_use: InvitationUse,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    invitation = await db.execute(
        select(FamilyInvitation)
        .where(and_(
            FamilyInvitation.code == invitation_use.code,
            FamilyInvitation.used == False,
            FamilyInvitation.expires_at > datetime.utcnow()
        ))
    )
    invitation = invitation.scalar_one_or_none()
    if not invitation:
        raise HTTPException(status_code=400, detail="邀请码无效或已过期")
    
    existing_member = await db.execute(
        select(FamilyMember)
        .where(and_(
            FamilyMember.user_id == current_user.id,
            FamilyMember.family_id == invitation.family_id
        ))
    )
    if existing_member.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="您已经是该家庭的成员")
    
    member = FamilyMember(
        user_id=current_user.id,
        family_id=invitation.family_id,
        role=UserFamilyRole.MEMBER
    )
    db.add(member)
    
    invitation.used = True
    invitation.used_by = current_user.id
    invitation.used_at = datetime.utcnow()
    
    await db.commit()
    
    if not current_user.current_family_id:
        current_user.current_family_id = invitation.family_id
        await db.commit()
    
    return {"message": "成功加入家庭"}


def build_location_tree(locations, parent_id=None):
    tree = []
    for loc in locations:
        if loc.parent_id == parent_id:
            children = build_location_tree(locations, loc.id)
            loc.children = children
            tree.append(loc)
    return sorted(tree, key=lambda x: x.sort_order)


@app.get("/locations/", response_model=List[LocationResponse])
async def get_locations(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    if not current_user.current_family_id:
        return []
    
    result = await db.execute(
        select(Location)
        .where(Location.family_id == current_user.current_family_id)
        .order_by(Location.sort_order)
    )
    locations = result.scalars().all()
    return build_location_tree(locations)


@app.post("/locations/", response_model=LocationResponse)
async def create_location(
    location: LocationCreate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    if not current_user.current_family_id:
        raise HTTPException(status_code=400, detail="请先选择一个家庭")
    
    level = 1
    if location.parent_id:
        parent = await db.execute(
            select(Location).where(Location.id == location.parent_id)
        )
        parent = parent.scalar_one_or_none()
        if not parent or parent.family_id != current_user.current_family_id:
            raise HTTPException(status_code=400, detail="无效的父位置")
        if parent.level >= 3:
            raise HTTPException(status_code=400, detail="最多支持三级位置")
        level = parent.level + 1
    
    max_order = await db.execute(
        select(func.max(Location.sort_order))
        .where(and_(
            Location.family_id == current_user.current_family_id,
            Location.parent_id == location.parent_id
        ))
    )
    max_order = max_order.scalar() or 0
    
    db_location = Location(
        family_id=current_user.current_family_id,
        name=location.name,
        level=level,
        parent_id=location.parent_id,
        sort_order=max_order + 1
    )
    db.add(db_location)
    await db.commit()
    await db.refresh(db_location)
    return db_location


@app.put("/locations/{location_id}/", response_model=LocationResponse)
async def update_location(
    location_id: int,
    location_update: LocationUpdate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    location = await db.execute(
        select(Location)
        .where(and_(
            Location.id == location_id,
            Location.family_id == current_user.current_family_id
        ))
    )
    location = location.scalar_one_or_none()
    if not location:
        raise HTTPException(status_code=404, detail="位置不存在")
    
    if location_update.name is not None:
        location.name = location_update.name
    if location_update.sort_order is not None:
        location.sort_order = location_update.sort_order
    
    await db.commit()
    await db.refresh(location)
    return location


@app.delete("/locations/{location_id}/")
async def delete_location(
    location_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    location = await db.execute(
        select(Location)
        .where(and_(
            Location.id == location_id,
            Location.family_id == current_user.current_family_id
        ))
    )
    location = location.scalar_one_or_none()
    if not location:
        raise HTTPException(status_code=404, detail="位置不存在")
    
    await db.delete(location)
    await db.commit()
    return {"message": "位置已删除"}


@app.post("/locations/reorder/")
async def reorder_locations(
    order: List[dict],
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    if not current_user.current_family_id:
        raise HTTPException(status_code=400, detail="请先选择一个家庭")
    
    for item in order:
        location = await db.execute(
            select(Location)
            .where(and_(
                Location.id == item["id"],
                Location.family_id == current_user.current_family_id
            ))
        )
        location = location.scalar_one_or_none()
        if location:
            location.sort_order = item["sort_order"]
    
    await db.commit()
    return {"message": "排序更新成功"}


@app.post("/items/", response_model=ItemResponse)
async def create_item(
    item: ItemCreate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    if not current_user.current_family_id:
        raise HTTPException(status_code=400, detail="请先选择一个家庭")
    
    db_item = Item(
        family_id=current_user.current_family_id,
        name=item.name,
        category=item.category,
        quantity=item.quantity,
        unit=item.unit,
        location_id=item.location_id,
        purchase_date=item.purchase_date,
        expiry_date=item.expiry_date,
        price=item.price,
        remarks=item.remarks,
        spec=item.spec,
        low_stock_threshold=item.low_stock_threshold,
        created_by=current_user.id
    )
    db.add(db_item)
    await db.commit()
    await db.refresh(db_item)
    
    if item.quantity > 0:
        record = InventoryRecord(
            item_id=db_item.id,
            operation_type=OperationType.IN,
            quantity_before=0,
            quantity_after=item.quantity,
            quantity_change=item.quantity,
            operator_id=current_user.id,
            remarks="初始入库"
        )
        db.add(record)
        await db.commit()
    
    return db_item


@app.get("/items/", response_model=List[ItemResponse])
async def get_items(
    category: Optional[str] = None,
    location_id: Optional[int] = None,
    show_deleted: bool = False,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    if not current_user.current_family_id:
        return []
    
    query = select(Item).where(Item.family_id == current_user.current_family_id)
    
    if not show_deleted:
        query = query.where(Item.is_deleted == False)
    if category:
        query = query.where(Item.category == category)
    if location_id:
        query = query.where(Item.location_id == location_id)
    
    result = await db.execute(query)
    return result.scalars().all()


@app.get("/items/{item_id}/", response_model=ItemResponse)
async def get_item(
    item_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    item = await db.execute(
        select(Item)
        .where(and_(
            Item.id == item_id,
            Item.family_id == current_user.current_family_id
        ))
    )
    item = item.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="物品不存在")
    return item


@app.put("/items/{item_id}/", response_model=ItemResponse)
async def update_item(
    item_id: int,
    item_update: ItemUpdate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    item = await db.execute(
        select(Item)
        .where(and_(
            Item.id == item_id,
            Item.family_id == current_user.current_family_id
        ))
    )
    item = item.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="物品不存在")
    
    old_quantity = item.quantity
    
    update_data = item_update.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(item, field, value)
    
    await db.commit()
    
    if item_update.quantity is not None and old_quantity != item_update.quantity:
        quantity_change = item_update.quantity - old_quantity
        operation_type = OperationType.IN if quantity_change > 0 else OperationType.OUT
        record = InventoryRecord(
            item_id=item.id,
            operation_type=operation_type,
            quantity_before=old_quantity,
            quantity_after=item_update.quantity,
            quantity_change=abs(quantity_change),
            operator_id=current_user.id,
            remarks="数量调整"
        )
        db.add(record)
        await db.commit()
    
    await db.refresh(item)
    return item


@app.delete("/items/{item_id}/")
async def delete_item(
    item_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    item = await db.execute(
        select(Item)
        .where(and_(
            Item.id == item_id,
            Item.family_id == current_user.current_family_id
        ))
    )
    item = item.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="物品不存在")
    
    item.is_deleted = True
    item.deleted_at = datetime.utcnow()
    await db.commit()
    return {"message": "物品已移至回收站"}


@app.post("/items/{item_id}/photo/")
async def upload_item_photo(
    item_id: int,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    item = await db.execute(
        select(Item)
        .where(and_(
            Item.id == item_id,
            Item.family_id == current_user.current_family_id
        ))
    )
    item = item.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="物品不存在")
    
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="请上传图片文件")
    
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
        raise HTTPException(status_code=400, detail="不支持的图片格式")
    
    filename = f"{uuid.uuid4()}{ext}"
    filepath = os.path.join("uploads", filename)
    
    async with aiofiles.open(filepath, 'wb') as out_file:
        content = await file.read()
        await out_file.write(content)
    
    if item.photo_path:
        old_path = os.path.join("uploads", item.photo_path)
        if os.path.exists(old_path):
            os.remove(old_path)
    
    item.photo_path = filename
    await db.commit()
    return {"photo_path": filename, "message": "照片上传成功"}


@app.post("/items/import/")
async def import_items(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    if not current_user.current_family_id:
        raise HTTPException(status_code=400, detail="请先选择一个家庭")
    
    content = await file.read()
    df = pd.read_csv(StringIO(content.decode('utf-8')))
    
    imported = 0
    for _, row in df.iterrows():
        item = Item(
            family_id=current_user.current_family_id,
            name=row.get('name', row.get('名称', '')),
            category=row.get('category', row.get('类别', '')),
            quantity=float(row.get('quantity', row.get('数量', 0))),
            unit=row.get('unit', row.get('单位', '')),
            price=float(row.get('price', row.get('价格', 0))) if pd.notna(row.get('price', row.get('价格'))) else None,
            spec=row.get('spec', row.get('规格', None)),
            remarks=row.get('remarks', row.get('备注', None)),
            low_stock_threshold=float(row.get('low_stock_threshold', row.get('低库存阈值', 0))),
            created_by=current_user.id
        )
        db.add(item)
        imported += 1
    
    await db.commit()
    return {"imported": imported}


@app.post("/inventory/record/", response_model=InventoryRecordResponse)
async def create_inventory_record(
    record: InventoryRecordCreate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    item = await db.execute(
        select(Item)
        .where(and_(
            Item.id == record.item_id,
            Item.family_id == current_user.current_family_id
        ))
    )
    item = item.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="物品不存在")
    
    quantity_before = item.quantity
    if record.operation_type in [OperationType.IN, OperationType.ADJUST]:
        quantity_after = quantity_before + record.quantity_change
    else:
        quantity_after = quantity_before - record.quantity_change
    
    if quantity_after < 0:
        raise HTTPException(status_code=400, detail="库存不足")
    
    item.quantity = quantity_after
    
    db_record = InventoryRecord(
        item_id=record.item_id,
        operation_type=record.operation_type,
        quantity_before=quantity_before,
        quantity_after=quantity_after,
        quantity_change=record.quantity_change,
        operator_id=current_user.id,
        remarks=record.remarks
    )
    db.add(db_record)
    await db.commit()
    await db.refresh(db_record)
    return db_record


@app.get("/inventory/records/{item_id}/", response_model=List[InventoryRecordResponse])
async def get_item_records(
    item_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    item = await db.execute(
        select(Item)
        .where(and_(
            Item.id == item_id,
            Item.family_id == current_user.current_family_id
        ))
    )
    if not item.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="物品不存在")
    
    result = await db.execute(
        select(InventoryRecord)
        .where(InventoryRecord.item_id == item_id)
        .order_by(InventoryRecord.created_at.desc())
    )
    return result.scalars().all()


@app.post("/inventory/records/{record_id}/reverse/", response_model=InventoryRecordResponse)
async def reverse_record(
    record_id: int,
    reverse: ReverseRecordCreate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    record = await db.execute(
        select(InventoryRecord)
        .join(Item)
        .where(and_(
            InventoryRecord.id == record_id,
            Item.family_id == current_user.current_family_id,
            InventoryRecord.is_reverse == False
        ))
    )
    record = record.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="记录不存在或已冲正")
    
    item = await db.get(Item, record.item_id)
    
    quantity_before = item.quantity
    if record.operation_type in [OperationType.IN, OperationType.ADJUST]:
        quantity_after = quantity_before - record.quantity_change
    else:
        quantity_after = quantity_before + record.quantity_change
    
    if quantity_after < 0:
        raise HTTPException(status_code=400, detail="冲正后库存将为负数")
    
    item.quantity = quantity_after
    
    reverse_record = InventoryRecord(
        item_id=record.item_id,
        operation_type=OperationType.ADJUST,
        quantity_before=quantity_before,
        quantity_after=quantity_after,
        quantity_change=abs(quantity_after - quantity_before),
        operator_id=current_user.id,
        remarks=f"冲正记录#{record.id}: {reverse.remarks or ''}",
        is_reverse=True,
        reversed_record_id=record.id
    )
    db.add(reverse_record)
    await db.commit()
    await db.refresh(reverse_record)
    return reverse_record


@app.get("/warnings/expiry/", response_model=ExpiryWarningResponse)
async def get_expiry_warnings(
    trigger_webhook: bool = False,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    if not current_user.current_family_id:
        return {"expired": [], "expiring_soon": []}
    
    now = datetime.utcnow()
    thirty_days_later = now + timedelta(days=30)
    
    warning_configs = await db.execute(
        select(CategoryWarning)
        .where(CategoryWarning.family_id == current_user.current_family_id)
    )
    warning_configs = {wc.category: wc.warning_days for wc in warning_configs.scalars().all()}
    
    items = await db.execute(
        select(Item)
        .where(and_(
            Item.family_id == current_user.current_family_id,
            Item.is_deleted == False,
            Item.expiry_date.isnot(None)
        ))
    )
    items = items.scalars().all()
    
    expired = []
    expiring_soon = []
    
    for item in items:
        if item.expiry_date < now:
            expired.append(item)
        else:
            warning_days = warning_configs.get(item.category, 7)
            warning_date = now + timedelta(days=warning_days)
            if item.expiry_date <= warning_date or item.expiry_date <= thirty_days_later:
                expiring_soon.append(item)
    
    if trigger_webhook and (expired or expiring_soon):
        await send_expiry_webhook(expired, expiring_soon)
    
    return {"expired": expired, "expiring_soon": expiring_soon}


@app.post("/warnings/category/", response_model=CategoryWarningResponse)
async def set_category_warning(
    warning: CategoryWarningCreate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    if not current_user.current_family_id:
        raise HTTPException(status_code=400, detail="请先选择一个家庭")
    
    existing = await db.execute(
        select(CategoryWarning)
        .where(and_(
            CategoryWarning.family_id == current_user.current_family_id,
            CategoryWarning.category == warning.category
        ))
    )
    existing = existing.scalar_one_or_none()
    
    if existing:
        existing.warning_days = warning.warning_days
        await db.commit()
        await db.refresh(existing)
        return existing
    
    db_warning = CategoryWarning(
        family_id=current_user.current_family_id,
        category=warning.category,
        warning_days=warning.warning_days
    )
    db.add(db_warning)
    await db.commit()
    await db.refresh(db_warning)
    return db_warning


@app.get("/statistics/")
async def get_statistics(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    if not current_user.current_family_id:
        return {
            "category_pie": {},
            "monthly_trend": {},
            "location_bar": {},
            "low_stock": []
        }
    
    category_result = await db.execute(
        select(Item.category, func.sum(Item.quantity), func.sum(Item.quantity * Item.price))
        .where(and_(
            Item.family_id == current_user.current_family_id,
            Item.is_deleted == False
        ))
        .group_by(Item.category)
    )
    category_pie = {}
    for row in category_result.all():
        category_pie[row[0]] = {"quantity": row[1], "value": row[2] or 0}
    
    monthly_result = await db.execute(
        select(
            func.strftime("%Y-%m", InventoryRecord.created_at),
            InventoryRecord.operation_type,
            func.sum(InventoryRecord.quantity_change)
        )
        .join(Item)
        .where(and_(
            Item.family_id == current_user.current_family_id,
            InventoryRecord.created_at >= datetime.utcnow() - timedelta(days=180)
        ))
        .group_by(func.strftime("%Y-%m", InventoryRecord.created_at), InventoryRecord.operation_type)
    )
    monthly_trend = {}
    for row in monthly_result.all():
        month = row[0]
        if month not in monthly_trend:
            monthly_trend[month] = {}
        monthly_trend[month][row[1]] = row[2]
    
    location_result = await db.execute(
        select(Location.name, func.sum(Item.quantity))
        .join(Item, isouter=True)
        .where(and_(
            Location.family_id == current_user.current_family_id,
            Location.level == 1,
            Item.is_deleted == False
        ))
        .group_by(Location.id)
    )
    location_bar = {row[0]: row[1] or 0 for row in location_result.all()}
    
    low_stock_result = await db.execute(
        select(Item)
        .where(and_(
            Item.family_id == current_user.current_family_id,
            Item.is_deleted == False,
            Item.quantity <= Item.low_stock_threshold,
            Item.low_stock_threshold > 0
        ))
    )
    low_stock = low_stock_result.scalars().all()
    
    return {
        "category_pie": category_pie,
        "monthly_trend": monthly_trend,
        "location_bar": location_bar,
        "low_stock": low_stock
    }


@app.get("/shopping-list/", response_model=List[ShoppingListResponse])
async def get_shopping_list(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    if not current_user.current_family_id:
        return []
    
    result = await db.execute(
        select(ShoppingList)
        .where(and_(
            ShoppingList.family_id == current_user.current_family_id,
            ShoppingList.is_purchased == False
        ))
        .order_by(ShoppingList.created_at.desc())
    )
    return result.scalars().all()


@app.post("/shopping-list/generate/")
async def generate_shopping_list(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    if not current_user.current_family_id:
        raise HTTPException(status_code=400, detail="请先选择一个家庭")
    
    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    
    records_result = await db.execute(
        select(
            Item.id,
            Item.name,
            Item.unit,
            Item.quantity,
            func.sum(InventoryRecord.quantity_change)
        )
        .select_from(Item)
        .join(InventoryRecord)
        .where(and_(
            Item.family_id == current_user.current_family_id,
            Item.is_deleted == False,
            InventoryRecord.operation_type.in_([OperationType.OUT, OperationType.CONSUME]),
            InventoryRecord.created_at >= thirty_days_ago
        ))
        .group_by(Item.id)
    )
    
    generated = 0
    for row in records_result.all():
        item_id, name, unit, current_qty, consumed = row
        avg_daily = consumed / 30 if consumed > 0 else 0
        
        if avg_daily > 0:
            days_left = current_qty / avg_daily if avg_daily > 0 else float('inf')
            if days_left <= 7:
                suggested_qty = avg_daily * 14
                
                existing = await db.execute(
                    select(ShoppingList)
                    .where(and_(
                        ShoppingList.family_id == current_user.current_family_id,
                        ShoppingList.item_name == name,
                        ShoppingList.is_purchased == False
                    ))
                )
                if not existing.scalar_one_or_none():
                    shopping_item = ShoppingList(
                        family_id=current_user.current_family_id,
                        item_name=name,
                        suggested_quantity=suggested_qty,
                        unit=unit,
                        is_auto_generated=True
                    )
                    db.add(shopping_item)
                    generated += 1
    
    await db.commit()
    return {"generated": generated}


@app.post("/shopping-list/", response_model=ShoppingListResponse)
async def add_shopping_item(
    item: ShoppingListCreate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    if not current_user.current_family_id:
        raise HTTPException(status_code=400, detail="请先选择一个家庭")
    
    shopping_item = ShoppingList(
        family_id=current_user.current_family_id,
        item_name=item.item_name,
        suggested_quantity=item.suggested_quantity,
        actual_quantity=item.actual_quantity,
        unit=item.unit,
        is_auto_generated=False
    )
    db.add(shopping_item)
    await db.commit()
    await db.refresh(shopping_item)
    return shopping_item


@app.post("/shopping-list/{item_id}/purchase/")
async def purchase_shopping_item(
    item_id: int,
    purchase: ShoppingListPurchase,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    shopping_item = await db.execute(
        select(ShoppingList)
        .where(and_(
            ShoppingList.id == item_id,
            ShoppingList.family_id == current_user.current_family_id
        ))
    )
    shopping_item = shopping_item.scalar_one_or_none()
    if not shopping_item:
        raise HTTPException(status_code=404, detail="购物清单项不存在")
    
    existing_item = await db.execute(
        select(Item)
        .where(and_(
            Item.family_id == current_user.current_family_id,
            Item.name == shopping_item.item_name,
            Item.unit == purchase.unit,
            Item.is_deleted == False
        ))
    )
    existing_item = existing_item.scalar_one_or_none()
    
    if existing_item:
        old_quantity = existing_item.quantity
        existing_item.quantity += purchase.actual_quantity
        if purchase.price:
            existing_item.price = purchase.price
        if purchase.location_id:
            existing_item.location_id = purchase.location_id
        
        record = InventoryRecord(
            item_id=existing_item.id,
            operation_type=OperationType.IN,
            quantity_before=old_quantity,
            quantity_after=existing_item.quantity,
            quantity_change=purchase.actual_quantity,
            operator_id=current_user.id,
            remarks="购物入库"
        )
        db.add(record)
        item_id = existing_item.id
    else:
        new_item = Item(
            family_id=current_user.current_family_id,
            name=shopping_item.item_name,
            category="未分类",
            quantity=purchase.actual_quantity,
            unit=purchase.unit,
            location_id=purchase.location_id,
            price=purchase.price,
            created_by=current_user.id
        )
        db.add(new_item)
        await db.flush()
        
        record = InventoryRecord(
            item_id=new_item.id,
            operation_type=OperationType.IN,
            quantity_before=0,
            quantity_after=purchase.actual_quantity,
            quantity_change=purchase.actual_quantity,
            operator_id=current_user.id,
            remarks="购物入库"
        )
        db.add(record)
        item_id = new_item.id
    
    shopping_item.is_purchased = True
    shopping_item.purchased_at = datetime.utcnow()
    shopping_item.purchased_by = current_user.id
    shopping_item.actual_quantity = purchase.actual_quantity
    shopping_item.unit = purchase.unit
    
    await db.commit()
    return {"message": "入库成功", "item_id": item_id}


@app.delete("/shopping-list/{item_id}/")
async def delete_shopping_item(
    item_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    shopping_item = await db.execute(
        select(ShoppingList)
        .where(and_(
            ShoppingList.id == item_id,
            ShoppingList.family_id == current_user.current_family_id
        ))
    )
    shopping_item = shopping_item.scalar_one_or_none()
    if not shopping_item:
        raise HTTPException(status_code=404, detail="购物清单项不存在")
    
    await db.delete(shopping_item)
    await db.commit()
    return {"message": "已删除"}


@app.get("/shopping-list/share/")
async def share_shopping_list(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    if not current_user.current_family_id:
        return {"text": ""}
    
    result = await db.execute(
        select(ShoppingList)
        .where(and_(
            ShoppingList.family_id == current_user.current_family_id,
            ShoppingList.is_purchased == False
        ))
        .order_by(ShoppingList.created_at.desc())
    )
    items = result.scalars().all()
    
    lines = ["🛒 家庭购物清单", "=" * 30]
    for item in items:
        qty = item.suggested_quantity or item.actual_quantity or ""
        line = f"• {item.item_name}"
        if qty:
            line += f" - {qty} {item.unit or ''}"
        lines.append(line.strip())
    
    return {"text": "\n".join(lines)}


@app.get("/", response_class=HTMLResponse)
async def root():
    with open("static/index.html", "r", encoding="utf-8") as f:
        return f.read()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8765)
