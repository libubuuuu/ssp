"""
产品 API 路由
"""
import uuid
import json
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict
from fastapi import APIRouter, HTTPException, Query, Depends
from app.database import get_db
from app.api.auth import get_current_user

router = APIRouter()


def _assert_owns_merchant(merchant_id: str, user: dict) -> None:
    """校验当前用户是该 merchant 的 owner 或 admin。

    五十七续:之前 products CUD 完全无鉴权 → 任何人匿名可建/改/删任何商家产品
    (OWASP Broken Access Control)。

    admin 跨商家 OK(运营场景);普通 user 必须是该 merchant 的 user_id。
    merchant 不存在时 404(不区分"merchant 不存在"和"非 owner",防泄漏)。
    """
    if user.get("role") == "admin":
        return
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM merchants WHERE id = ?", (merchant_id,))
        row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="商家不存在")
    if str(row[0]) != str(user["id"]):
        raise HTTPException(status_code=403, detail="非该商家产品所有者")


def _get_product_merchant_id(product_id: str) -> Optional[str]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT merchant_id FROM products WHERE id = ?", (product_id,))
        row = cursor.fetchone()
    return row[0] if row else None


# 请求/响应模型
# 这些 model 有 model_3d_url 字段,跟 pydantic v2 的 model_ 受保护命名空间冲突。
# 用 protected_namespaces=() 关掉保护(我们没用 model_validate 之类的方法)。
_PRODUCT_MODEL_CONFIG = ConfigDict(protected_namespaces=())


class ProductCreate(BaseModel):
    model_config = _PRODUCT_MODEL_CONFIG
    merchant_id: str
    name: str
    description: Optional[str] = None
    category: str
    gender: str
    price: float
    images: Optional[List[str]] = None
    model_3d_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    sizes: Optional[List[str]] = None
    stock: int = 0


class ProductUpdate(BaseModel):
    model_config = _PRODUCT_MODEL_CONFIG
    name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    gender: Optional[str] = None
    price: Optional[float] = None
    images: Optional[List[str]] = None
    model_3d_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    sizes: Optional[List[str]] = None
    stock: Optional[int] = None
    is_published: Optional[bool] = None


class ProductResponse(BaseModel):
    # v2 风格:合并 protected_namespaces 关掉 + from_attributes 替代 v1 的 class Config
    model_config = ConfigDict(protected_namespaces=(), from_attributes=True)
    id: str
    merchant_id: str
    name: str
    description: Optional[str]
    category: str
    gender: str
    price: float
    images: Optional[List[str]]
    model_3d_url: Optional[str]
    thumbnail_url: Optional[str]
    sizes: Optional[List[str]]
    stock: int
    is_published: bool
    created_at: str
    updated_at: str


# 辅助函数
def row_to_product(row) -> dict:
    """将数据库行转换为产品字典"""
    return {
        "id": row["id"],
        "merchant_id": row["merchant_id"],
        "name": row["name"],
        "description": row["description"],
        "category": row["category"],
        "gender": row["gender"],
        "price": row["price"],
        "images": json.loads(row["images"]) if row["images"] else None,
        "model_3d_url": row["model_3d_url"],
        "thumbnail_url": row["thumbnail_url"],
        "sizes": json.loads(row["sizes"]) if row["sizes"] else None,
        "stock": row["stock"],
        "is_published": bool(row["is_published"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


# API 路由
@router.get("", response_model=List[ProductResponse])
async def list_products(
    category: Optional[str] = Query(None),
    gender: Optional[str] = Query(None),
    min_price: Optional[float] = Query(None),
    max_price: Optional[float] = Query(None),
    is_published: Optional[bool] = Query(True),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    """获取产品列表"""
    with get_db() as conn:
        cursor = conn.cursor()

        query = "SELECT * FROM products WHERE 1=1"
        params = []

        if category:
            query += " AND category = ?"
            params.append(category)

        if gender:
            query += " AND gender = ?"
            params.append(gender)

        if min_price is not None:
            query += " AND price >= ?"
            params.append(min_price)

        if max_price is not None:
            query += " AND price <= ?"
            params.append(max_price)

        if is_published is not None:
            query += " AND is_published = ?"
            params.append(1 if is_published else 0)

        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, skip])

        cursor.execute(query, params)
        rows = cursor.fetchall()

        return [row_to_product(row) for row in rows]


@router.get("/{product_id}", response_model=ProductResponse)
async def get_product(product_id: str):
    """获取单个产品详情"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM products WHERE id = ?", (product_id,))
        row = cursor.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="产品不存在")

        return row_to_product(row)


@router.post("", response_model=ProductResponse)
async def create_product(product: ProductCreate, current_user: dict = Depends(get_current_user)):
    """创建产品(必须是该 merchant 的 owner 或 admin)"""
    _assert_owns_merchant(product.merchant_id, current_user)
    product_id = str(uuid.uuid4())
    now = datetime.now().isoformat()

    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute("""
        INSERT INTO products (
            id, merchant_id, name, description, category, gender,
            price, images, model_3d_url, thumbnail_url, sizes,
            stock, is_published, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            product_id,
            product.merchant_id,
            product.name,
            product.description,
            product.category,
            product.gender,
            product.price,
            json.dumps(product.images) if product.images else None,
            product.model_3d_url,
            product.thumbnail_url,
            json.dumps(product.sizes) if product.sizes else None,
            product.stock,
            False,  # 默认未发布
            now,
            now,
        ))

        conn.commit()

        # 返回创建的产品
        cursor.execute("SELECT * FROM products WHERE id = ?", (product_id,))
        row = cursor.fetchone()
        return row_to_product(row)


@router.put("/{product_id}", response_model=ProductResponse)
async def update_product(product_id: str, product: ProductUpdate, current_user: dict = Depends(get_current_user)):
    """更新产品(必须是该产品 merchant 的 owner 或 admin)"""
    merchant_id = _get_product_merchant_id(product_id)
    if not merchant_id:
        raise HTTPException(status_code=404, detail="产品不存在")
    _assert_owns_merchant(merchant_id, current_user)

    with get_db() as conn:
        cursor = conn.cursor()

        # 检查产品是否存在(已上游 check 但保留二次防御)
        cursor.execute("SELECT id FROM products WHERE id = ?", (product_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="产品不存在")

        # 构建更新语句
        updates = []
        params = []

        if product.name is not None:
            updates.append("name = ?")
            params.append(product.name)

        if product.description is not None:
            updates.append("description = ?")
            params.append(product.description)

        if product.category is not None:
            updates.append("category = ?")
            params.append(product.category)

        if product.gender is not None:
            updates.append("gender = ?")
            params.append(product.gender)

        if product.price is not None:
            updates.append("price = ?")
            params.append(product.price)

        if product.images is not None:
            updates.append("images = ?")
            params.append(json.dumps(product.images))

        if product.model_3d_url is not None:
            updates.append("model_3d_url = ?")
            params.append(product.model_3d_url)

        if product.thumbnail_url is not None:
            updates.append("thumbnail_url = ?")
            params.append(product.thumbnail_url)

        if product.sizes is not None:
            updates.append("sizes = ?")
            params.append(json.dumps(product.sizes))

        if product.stock is not None:
            updates.append("stock = ?")
            params.append(product.stock)

        if product.is_published is not None:
            updates.append("is_published = ?")
            params.append(1 if product.is_published else 0)

        if not updates:
            raise HTTPException(status_code=400, detail="没有需要更新的字段")

        updates.append("updated_at = ?")
        params.append(datetime.now().isoformat())

        params.append(product_id)

        query = f"UPDATE products SET {', '.join(updates)} WHERE id = ?"
        cursor.execute(query, params)
        conn.commit()

        # 返回更新后的产品
        cursor.execute("SELECT * FROM products WHERE id = ?", (product_id,))
        row = cursor.fetchone()
        return row_to_product(row)


@router.delete("/{product_id}")
async def delete_product(product_id: str, current_user: dict = Depends(get_current_user)):
    """删除产品(必须是该产品 merchant 的 owner 或 admin)"""
    merchant_id = _get_product_merchant_id(product_id)
    if not merchant_id:
        raise HTTPException(status_code=404, detail="产品不存在")
    _assert_owns_merchant(merchant_id, current_user)

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM products WHERE id = ?", (product_id,))
        conn.commit()
        return {"message": "产品已删除", "id": product_id}


@router.get("/merchant/{merchant_id}", response_model=List[ProductResponse])
async def list_merchant_products(
    merchant_id: str,
    is_published: Optional[bool] = Query(None),
):
    """获取商家的产品列表"""
    with get_db() as conn:
        cursor = conn.cursor()

        query = "SELECT * FROM products WHERE merchant_id = ?"
        params = [merchant_id]

        if is_published is not None:
            query += " AND is_published = ?"
            params.append(1 if is_published else 0)

        query += " ORDER BY created_at DESC"

        cursor.execute(query, params)
        rows = cursor.fetchall()

        return [row_to_product(row) for row in rows]
