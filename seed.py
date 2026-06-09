"""Seed sample products. Run: python seed.py"""
from app.database import Base, engine, SessionLocal
from app.models import Product
from app.utils import slugify

Base.metadata.create_all(bind=engine)

SAMPLES = [
    dict(
        name="Handwoven Woolen Shawl",
        category="woolen",
        material="100% Handmade Wool",
        description="Warm, soft handwoven shawl made by local artisans. Perfect for winters.",
        price=1499, mrp=1999, stock=12, is_featured=True,
        images=["https://images.unsplash.com/photo-1520903074185-8eca362b3dce?w=800"],
    ),
    dict(
        name="Krishna Idol - Brass",
        category="god",
        material="Pure Brass",
        description="Handcrafted brass idol of Lord Krishna. Ideal for home temple and gifting.",
        price=2499, mrp=2999, stock=8, is_featured=True,
        images=["https://images.unsplash.com/photo-1609619385002-f40f1df9b7eb?w=800"],
    ),
    dict(
        name="Rudraksha Mala Necklace",
        category="jewellery",
        material="Natural Rudraksha Beads",
        description="Sacred 108-bead Rudraksha mala for meditation and daily wear.",
        price=799, mrp=1099, stock=25, is_featured=True,
        images=["https://images.unsplash.com/photo-1611591437281-460bfbe1220a?w=800"],
    ),
    dict(
        name="Cotton Handloom Kurta",
        category="clothes",
        material="Pure Cotton Handloom",
        description="Breathable handloom cotton kurta, block-printed by hand.",
        price=1199, mrp=1599, stock=18, is_featured=True,
        images=["https://images.unsplash.com/photo-1564859228273-274232fdb516?w=800"],
    ),
    dict(
        name="Woolen Baby Booties",
        category="woolen",
        material="Soft Merino Wool",
        description="Hand-knitted woolen booties for babies. Cozy and gentle on skin.",
        price=349, mrp=499, stock=40,
        images=["https://images.unsplash.com/photo-1515488042361-ee00e0ddd4e4?w=800"],
    ),
    dict(
        name="Ganesha Wall Hanging",
        category="god",
        material="Handpainted Terracotta",
        description="Auspicious Ganesha terracotta wall hanging, handpainted in vibrant colors.",
        price=899, mrp=1199, stock=15,
        images=["https://images.unsplash.com/photo-1626197031507-c17099753214?w=800"],
    ),
    dict(
        name="Silver Oxidised Earrings",
        category="jewellery",
        material="Oxidised Silver Alloy",
        description="Traditional handmade oxidised earrings with ethnic motifs.",
        price=599, mrp=899, stock=30,
        images=["https://images.unsplash.com/photo-1535632066927-ab7c9ab60908?w=800"],
    ),
    dict(
        name="Woolen Cardigan - Hand Knit",
        category="woolen",
        material="Pure Wool",
        description="Cozy hand-knitted woolen cardigan with wooden buttons.",
        price=1799, mrp=2499, stock=10,
        images=["https://images.unsplash.com/photo-1434389677669-e08b4cac3105?w=800"],
    ),
]


def run():
    db = SessionLocal()
    try:
        for s in SAMPLES:
            exists = db.query(Product).filter(Product.name == s["name"]).first()
            if exists:
                continue
            p = Product(**s)
            p.slug = slugify(s["name"])
            db.add(p)
        db.commit()
        print(f"Seeded products. Total now: {db.query(Product).count()}")
    finally:
        db.close()


if __name__ == "__main__":
    run()
