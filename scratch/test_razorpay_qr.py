import sys, os, json
sys.path.insert(0, os.path.abspath('.'))
import asyncio
from dotenv import load_dotenv
load_dotenv()

from src.services.razorpay_service import create_dynamic_qr_code_async
from src.core.pricing_engine import calculate_exact_fee

async def test_razorpay():
    exact_bill = calculate_exact_fee("ladies_dance", "1 Month")
    print("Exact bill:", exact_bill)
    final_amount = exact_bill["final_payable_amount"]
    final_amount_paise = exact_bill.get("final_amount_paise") or (final_amount * 100)
    
    print("Creating Razorpay QR...")
    try:
        qr_data = await create_dynamic_qr_code_async(
            amount=final_amount,
            amount_in_paise=final_amount_paise,
            course_id="ladies_dance",
            duration="1 Month",
            timing="11:00 AM to 12:00 PM",
            teacher="Aditi Vyas",
            days="Monday to Friday",
            customer_phone="919811134113",
            name="Sensationz - Ladies Dance",
            description="Ladies Dance (1 Month) Enrollment",
            close_by_minutes=30,
        )
        print("Razorpay QR SUCCESS:", qr_data)
    except Exception as e:
        import traceback
        traceback.print_exc()

asyncio.run(test_razorpay())
