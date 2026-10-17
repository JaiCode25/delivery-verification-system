from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.enums import OrderStatus, Platform


class ProductReferenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    order_id: int
    order_item_id: int
    relative_path: str
    original_filename: str
    file_size_bytes: int
    mime_type: str
    created_at: datetime


class ProductReferenceListResponse(BaseModel):
    order_id: str
    total_references: int
    references: List[ProductReferenceRead]


class OrderItemBase(BaseModel):
    product_name: str = Field(..., min_length=1, max_length=255)
    brand: str = Field(..., min_length=1, max_length=100)
    model: str = Field(..., min_length=1, max_length=100)
    colour: str = Field(..., min_length=1, max_length=50)
    expected_quantity: int = Field(default=1, ge=1)
    product_url: Optional[str] = None
    reference_image_path: Optional[str] = None


class OrderItemCreate(OrderItemBase):
    pass


class OrderItemRead(OrderItemBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: Optional[datetime] = None
    references: List[ProductReferenceRead] = []


class OrderBase(BaseModel):
    order_id: str = Field(..., min_length=1, max_length=100)
    tracking_id: str = Field(..., min_length=1, max_length=100)
    barcode_value: str = Field(..., min_length=1, max_length=100)
    platform: Platform
    customer_name: str = Field(..., min_length=1, max_length=200)
    customer_email: str = Field(..., pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=200)
    delivery_address: str = Field(..., min_length=5)
    order_status: OrderStatus = OrderStatus.DELIVERED
    delivered_at: Optional[datetime] = None


class OrderCreate(OrderBase):
    items: List[OrderItemCreate] = Field(default_factory=list, min_length=1)


class OrderRead(OrderBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    items: List[OrderItemRead] = []
    references: List[ProductReferenceRead] = []


class OrderSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    order_id: str
    tracking_id: str
    barcode_value: str
    platform: Platform
    customer_name: str
    order_status: OrderStatus
    delivered_at: Optional[datetime] = None
    item_count: int = 0
    items_summary: List[str] = []


class OrderListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    orders: List[OrderRead]
