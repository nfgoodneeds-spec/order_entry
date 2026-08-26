import streamlit as st
import pandas as pd
import json
from datetime import datetime
import google.generativeai as genai
from PIL import Image

# --------------------------------------------------
# 初期設定
# --------------------------------------------------
st.set_page_config(page_title="受発注DXタブレットアプリ", layout="wide")

# APIキー設定（Streamlit secrets または 直接入力）
GEMINI_API_KEY = st.secrets.get("GEMINI_API_KEY", "YOUR_GEMINI_API_KEY")
if GEMINI_API_KEY != "YOUR_GEMINI_API_KEY":
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-1.5-flash")

# 商品マスタ（品番: [品名, 標準単価]）
ITEM_MASTER = {
    "A-101": {"name": "超音波ノズル先端部品", "price": 12000},
    "A-102": {"name": "高圧ホース 3m", "price": 8500},
    "B-201": {"name": "専用洗浄溶剤 5L", "price": 4500},
    "B-202": {"name": "皮革用リカラー染料（ブラック）", "price": 3200},
    "C-301": {"name": "交換用パッキンセット", "price": 1500},
}
CUSTOMER_LIST = ["株式会社サンプル商事", "東京リユース工業", "埼玉メンテナンス", "その他（新規）"]

# セッション状態の初期化
if "current_order" not in st.session_state:
    st.session_state.current_order = None
if "phone_cart" not in st.session_state:
    st.session_state.phone_cart = []  # 電話受付の明細リスト

# --------------------------------------------------
# AI抽出用プロンプト関数
# --------------------------------------------------
def extract_order_info(input_data, is_image=False):
    system_prompt = """
    あなたは受発注伝票の解析アシスタントです。
    入力された情報（注文書画像またはメール本文）から、以下の情報を抽出し、必ずJSON形式のみで出力してください。
    
    【出力フォーマット】
    {
      "customer_name": "顧客名（不明なら空文字）",
      "items": [
        {"item_name": "商品名や品番", "quantity": 数値, "unit": "個/本など"}
      ],
      "delivery_date": "希望納期（YYYY-MM-DD形式、不明なら空文字）",
      "notes": "特記事項・備考"
    }
    """
    try:
        if is_image:
            response = model.generate_content([system_prompt, input_data])
        else:
            response = model.generate_content(f"{system_prompt}\n\n【対象テキスト】\n{input_data}")
            
        text = response.text.replace("```json", "").replace("```", "").strip()
        return json.loads(text)
    except Exception as e:
        st.error(f"AI解析エラー: {e}")
        return None

# --------------------------------------------------
# UI画面構成
# --------------------------------------------------
st.title("📦 受発注登録・確認ダッシュボード")

tab_fax, tab_mail, tab_phone, tab_list = st.tabs([
    "📠 FAX（写真・スキャン）", 
    "✉️ メール（コピペ解析）", 
    "📞 電話（複数商品入力）", 
    "📋 登録済み注文一覧"
])

# ==========================================
# 1. FAX（写真・スキャン画像）
# ==========================================
with tab_fax:
    st.subheader("FAX注文書の写真・スキャン取り込み")
    col1, col2 = st.columns([1, 1])
    
    with col1:
        uploaded_file = st.file_uploader("注文書画像を選択（またはカメラ撮影）", type=["jpg", "jpeg", "png", "pdf"], key="fax_upload")
        if uploaded_file:
            img = Image.open(uploaded_file)
            st.image(img, caption="アップロードされた注文書", use_container_width=True)
            
            if st.button("🤖 AIで自動読取を実行", key="btn_fax_ai"):
                with st.spinner("Geminiが手書き・活字を解析中..."):
                    result = extract_order_info(img, is_image=True)
                    if result:
                        st.session_state.current_order = result
                        st.success("解析が完了しました！右側で内容を確認してください。")

    with col2:
        st.subheader("📝 読取結果の確認・修正")
        if st.session_state.current_order:
            order = st.session_state.current_order
            c_name = st.text_input("顧客名", value=order.get("customer_name", ""), key="fax_cname")
            d_date = st.text_input("希望納期", value=order.get("delivery_date", ""), key="fax_ddate")
            
            st.write("▼ 注文明細")
            items = order.get("items", [])
            for i, itm in enumerate(items):
                c_i1, c_i2, c_i3 = st.columns([3, 1, 1])
                c_i1.text_input(f"品名/品番 #{i+1}", value=itm.get("item_name", ""), key=f"fax_item_{i}")
                c_i2.number_input(f"数量 #{i+1}", value=int(itm.get("quantity", 1)), min_value=1, key=f"fax_qty_{i}")
                c_i3.text_input(f"単位 #{i+1}", value=itm.get("unit", "個"), key=f"fax_unit_{i}")
            
            st.text_area("備考", value=order.get("notes", ""), key="fax_notes")
            if st.button("✅ この内容で注文を確定・保存", key="btn_save_fax"):
                st.success("注文データを保存しました")

# ==========================================
# 2. メール（コピペ）
# ==========================================
with tab_mail:
    st.subheader("メール本文のコピペ解析")
    mail_text = st.text_area("メール本文をそのまま貼り付けてください", height=150, placeholder="〇〇商事です。高圧ホース3mを2本、洗浄溶剤を1缶送ってください。")
    
    if st.button("🤖 メールから注文内容を抽出", key="btn_mail_ai"):
        if mail_text:
            with st.spinner("AI解析中..."):
                result = extract_order_info(mail_text, is_image=False)
                if result:
                    st.session_state.current_order = result
                    st.json(result)

# ==========================================
# 3. 電話受付（複数商品・カート方式）
# ==========================================
with tab_phone:
    st.subheader("📞 電話受付 - 複数商品スピード登録")
    
    col_c1, col_c2 = st.columns(2)
    customer = col_c1.selectbox("顧客名", CUSTOMER_LIST, key="phone_customer")
    req_date = col_c2.date_input("希望納期", key="phone_date")
    
    st.markdown("---")
    st.write("### 🛒 商品の追加")
    
    col_p1, col_p2, col_p3, col_p4 = st.columns([3, 1, 1, 1])
    
    selected_code = col_p1.selectbox(
        "商品選択（コード・品名）", 
        options=list(ITEM_MASTER.keys()), 
        format_func=lambda x: f"【{x}】 {ITEM_MASTER[x]['name']}"
    )
    
    default_price = ITEM_MASTER[selected_code]["price"]
    qty = col_p2.number_input("数量", min_value=1, value=1, key="add_qty")
    unit_price = col_p3.number_input("単価（円）", value=default_price, step=100, key="add_price")
    
    with col_p4:
        st.write("")
        st.write("")
        if st.button("＋ 明細に追加", use_container_width=True):
            st.session_state.phone_cart.append({
                "code": selected_code,
                "name": ITEM_MASTER[selected_code]["name"],
                "qty": qty,
                "price": unit_price,
                "subtotal": qty * unit_price
            })
            st.rerun()

    # 明細一覧表示
    if st.session_state.phone_cart:
        st.write("### 📋 注文明細一覧")
        cart_df = pd.DataFrame(st.session_state.phone_cart)
        
        # テーブル表示用に見やすく整形
        display_df = cart_df.rename(columns={
            "code": "品番", "name": "品名", "qty": "数量", "price": "単価(円)", "subtotal": "小計(円)"
        })
        st.dataframe(display_df, use_container_width=True)
        
        total_amount = sum(item["subtotal"] for item in st.session_state.phone_cart)
        st.markdown(f"#### 💰 合計金額: **¥{total_amount:,}**（税別）")
        
        col_act1, col_act2 = st.columns([1, 4])
        if col_act1.button("🗑️ 明細をクリア"):
            st.session_state.phone_cart = []
            st.rerun()
            
        phone_notes = st.text_input("通話メモ・特記事項", key="phone_note_input")
        
        if st.button("✅ 複数商品の電話注文を確定・登録", type="primary", use_container_width=True):
            st.success(f"【登録完了】{customer} 様の注文（合計 {len(st.session_state.phone_cart)} 品目 / ¥{total_amount:,}）を保存しました！")
            st.session_state.phone_cart = []  # 登録後にカートを空にする
    else:
        st.info("上の「＋ 明細に追加」ボタンを押して商品を明細に入れてください。何品でも追加できます。")

# ==========================================
# 4. 登録済み一覧
# ==========================================
with tab_list:
    st.subheader("登録済みデータ一覧")
    st.info("ここに登録された受発注データが蓄積されます。")
