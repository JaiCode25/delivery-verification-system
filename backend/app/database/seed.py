from datetime import datetime, timezone
from sqlalchemy.orm import Session

from app.database.connection import SessionLocal, init_db
from app.database.models import Order, OrderItem
from app.schemas.enums import OrderStatus, Platform

DEMO_ORDERS = [
    {
        "order_id": "DEMO-OD-FLIPKART-88214",
        "tracking_id": "FMPC6414359435",
        "barcode_value": "FMPC6414359435",
        "platform": Platform.FLIPKART.value,
        "customer_name": "Demo Student User (Testing)",
        "customer_email": "demo-flipkart@example.com",
        "delivery_address": "Room 101, Demonstration Hall, College Campus, Pune 411037",
        "order_status": OrderStatus.DELIVERED.value,
        "delivered_at": datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc),
        "items": [
            {
                "product_name": "Rocsil Analog Watch For Women",
                "brand": "Rocsil",
                "model": "105",
                "colour": "Black",
                "expected_quantity": 1,
                "product_url": "https://www.flipkart.com/demo/item-105",
            }
        ],
    },
    {
        "order_id": "DEMO-AMZ-402-9981245-11002",
        "tracking_id": "AMZN-IN-981240182",
        "barcode_value": "AMZN-IN-981240182",
        "platform": Platform.AMAZON.value,
        "customer_name": "Demo Faculty Member",
        "customer_email": "demo-amazon@example.com",
        "delivery_address": "Faculty Quarters Block B, College Campus, Pune 411037",
        "order_status": OrderStatus.DELIVERED.value,
        "delivered_at": datetime(2026, 9, 8, 15, 30, 0, tzinfo=timezone.utc),
        "items": [
            {
                "product_name": "Sony WH-1000XM5 Wireless Headphones",
                "brand": "Sony",
                "model": "WH-1000XM5",
                "colour": "Silver",
                "expected_quantity": 1,
                "product_url": "https://www.amazon.in/demo/sony-xm5",
            }
        ],
    },
    {
        "order_id": "DEMO-BLK-99381023",
        "tracking_id": "BLK-PNQ-5521",
        "barcode_value": "BLK-PNQ-5521",
        "platform": Platform.BLINKIT.value,
        "customer_name": "Demo Lab Assistant",
        "customer_email": "demo-blinkit@example.com",
        "delivery_address": "ECE Laboratory, Ground Floor, Academic Complex, Pune 411037",
        "order_status": OrderStatus.DELIVERED.value,
        "delivered_at": datetime(2026, 9, 10, 9, 45, 0, tzinfo=timezone.utc),
        "items": [
            {
                "product_name": "Nescafe Classic Coffee 200g Jar",
                "brand": "Nescafe",
                "model": "Classic 200g",
                "colour": "Brown",
                "expected_quantity": 2,
                "product_url": "https://blinkit.com/demo/coffee",
            }
        ],
    },
]


def seed_demonstration_orders(session: Session = None) -> list[str]:
    """Idempotently seed demonstration orders into SQLite database.

    Returns list of order_ids that were newly inserted.
    """
    db = session or SessionLocal()
    close_after = session is None

    inserted_order_ids: list[str] = []
    try:
        init_db()
        for data in DEMO_ORDERS:
            existing = db.query(Order).filter(Order.order_id == data["order_id"]).first()
            if existing is None:
                items_data = data["items"]
                order = Order(
                    order_id=data["order_id"],
                    tracking_id=data["tracking_id"],
                    barcode_value=data["barcode_value"],
                    platform=data["platform"],
                    customer_name=data["customer_name"],
                    customer_email=data["customer_email"],
                    delivery_address=data["delivery_address"],
                    order_status=data["order_status"],
                    delivered_at=data["delivered_at"],
                )
                for item_dict in items_data:
                    item = OrderItem(
                        product_name=item_dict["product_name"],
                        brand=item_dict["brand"],
                        model=item_dict["model"],
                        colour=item_dict["colour"],
                        expected_quantity=item_dict["expected_quantity"],
                        product_url=item_dict.get("product_url"),
                    )
                    order.items.append(item)
                db.add(order)
                inserted_order_ids.append(data["order_id"])
        db.commit()
    finally:
        if close_after:
            db.close()

    return inserted_order_ids


if __name__ == "__main__":
    newly_added = seed_demonstration_orders()
    print(f"Seed completed. Newly added orders: {newly_added}")
