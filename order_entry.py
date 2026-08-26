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

# APIキー設定（環境変数またはst.secrets推奨）
GEMINI_API_KEY = "AIzaSyBkraxElwA3Fnx5muYa9X_vwaqMA-97uks"
if GEMINI_API_KEY != "YOUR_GEMINI_API_KEY":
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-1.5-flash")

# 商品マスタのモック（実際は自社DBやCSVから読み込み）
ITEM_MASTER = {
    "A-101": "超音波ノズル先端部品",
    "A-102": "高圧ホース 3m",
    "B-201": "専用洗浄溶剤 5L",
    "B-202": "皮革用リカラー染料（ブラック）",
    "C-301": "交換用パッキンセット",
}
CUSTOMER_LIST = ["株式会社サンプル商事", "東京リユース工業", "埼玉メンテナンス", "その他（新規）"]

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
            
        # JSON部分のみ抽出
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
    "📞 電話（直接入力・検索）", 
    "📋 登録済み注文一覧"
])

# 共通の登録データ保持用
if "current_order" not in st.session_state:
    st.session_state.current_order = None

# ==========================================
# 1. FAX（写真・スキャン画像）
# ==========================================
with tab_fax:
    st.subheader("FAX注文書の写真・スキャン取り込み")
    col1, col2 = st.columns([1, 1])
    
    with col1:
        # タブレットのカメラ直接撮影またはファイル選択
        uploaded_file = st.file_uploader("注文書画像を選択（またはカメラ撮影）", type=["jpg", "jpeg", "png", "pdf"])
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
            c_name = st.text_input("顧客名", value=order.get("customer_name", ""))
            d_date = st.text_input("希望納期", value=order.get("delivery_date", ""))
            
            st.write("▼ 注文明細")
            items = order.get("items", [])
            edited_items = []
            for i, itm in enumerate(items):
                c_i1, c_i2, c_i3 = st.columns([3, 1, 1])
                name = c_i1.text_input(f"品名/品番 #{i+1}", value=itm.get("item_name", ""), key=f"fax_item_{i}")
                qty = c_i2.number_input(f"数量 #{i+1}", value=int(itm.get("quantity", 1)), min_value=1, key=f"fax_qty_{i}")
                unit = c_i3.text_input(f"単位 #{i+1}", value=itm.get("unit", "個"), key=f"fax_unit_{i}")
                edited_items.append({"item_name": name, "quantity": qty, "unit": unit})
            
            notes = st.text_area("備考", value=order.get("notes", ""))
            
            if st.button("✅ この内容で注文を確定・保存", key="btn_save_fax"):
                st.success("注文データを保存しました（CSV/DB連携）")

# ==========================================
# 2. メール（コピペ）
# ==========================================
with tab_mail:
    st.subheader("メール本文のコピペ解析")
    mail_text = st.text_area("メール本文をそのまま貼り付けてください", height=200, placeholder="お世話になっております。〇〇商事の田中です。高圧ホース3mを2本と、専用洗浄溶剤を1缶至急送ってください。納期は来週月曜希望です。")
    
    if st.button("🤖 メールから注文内容を抽出", key="btn_mail_ai"):
        if mail_text:
            with st.spinner("AI解析中..."):
                result = extract_order_info(mail_text, is_image=False)
                if result:
                    st.session_state.current_order = result
                    st.json(result)

# ==========================================
# 3. 電話（直接入力・検索・プルダウン）
# ==========================================
with tab_phone:
    st.subheader("電話受付 - スピード入力")
    
    with st.form("phone_order_form"):
        col_c1, col_c2 = st.columns(2)
        customer = col_c1.selectbox("顧客選択（プルダウン）", CUSTOMER_LIST)
        order_date = col_c2.date_input("受付日", datetime.today())
        
        st.divider()
        st.write("🛒 商品選択・検索")
        
        # 品番検索 / プルダウン
        selected_code = st.selectbox(
            "商品マスタから選択（コード・品名）", 
            options=list(ITEM_MASTER.keys()), 
            format_func=lambda x: f"【{x}】 {ITEM_MASTER[x]}"
        )
        
        col_p1, col_p2, col_p3 = st.columns([2, 1, 2])
        qty = col_p1.number_input("数量", min_value=1, value=1)
        unit_price = col_p2.number_input("単価（円）", value=5000, step=100)
        req_date = col_p3.date_input("希望納期")
        
        phone_notes = st.text_input("通話メモ・特記事項（例: 〇〇様より口頭依頼。午前着希望）")
        
        submitted = st.form_submit_button("📞 電話注文を即時登録")
        if submitted:
            st.success(f"【登録完了】{customer} 様 / {ITEM_MASTER[selected_code]} × {qty}")

# ==========================================
# 4. 登録済み一覧（CSVエクスポート用）
# ==========================================
with tab_list:
    st.subheader("登録済みデータ一覧・基幹連携")
    st.info("タブレット上で確認・修正されたデータがここに集約され、販売管理システム（ERP）用のCSVとしてワンクリック出力できます。")