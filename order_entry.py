import streamlit as st
import pandas as pd
import json
import os
import io
from datetime import datetime
import google.generativeai as genai
from PIL import Image

# --------------------------------------------------
# 初期設定
# --------------------------------------------------
st.set_page_config(page_title="受発注DXタブレットアプリ", layout="wide")

ORDERS_CSV = "orders_data.csv"
ITEMS_CSV = "items.csv"
CUSTOMERS_CSV = "customers.csv"
COLUMNS = ["注文日時", "受付種別", "顧客名", "電話番号", "住所", "品番", "品名", "数量", "単価", "小計", "希望納期", "備考"]

# --------------------------------------------------
# マスタデータの読み込み（CSV連動 ＆ 自動初期化）
# --------------------------------------------------
@st.cache_data(ttl=60)
def load_item_master():
    """商品マスタCSVの読み込み"""
    if not os.path.exists(ITEMS_CSV):
        # 初回サンプルデータの自動生成
        sample_items = pd.DataFrame([
            {"品番": "A-101", "品名": "超音波ノズル先端部品", "標準単価": 12000},
            {"品番": "A-102", "品名": "高圧ホース 3m", "標準単価": 8500},
            {"品番": "B-201", "品名": "専用洗浄溶剤 5L", "標準単価": 4500},
            {"品番": "B-202", "品名": "皮革用リカラー染料（ブラック）", "標準単価": 3200},
            {"品番": "C-301", "品名": "交換用パッキンセット", "標準単価": 1500},
        ])
        sample_items.to_csv(ITEMS_CSV, index=False, encoding="utf-8-sig")
        return sample_items
    try:
        return pd.read_csv(ITEMS_CSV, encoding="utf-8-sig", dtype={"品番": str})
    except Exception:
        return pd.DataFrame(columns=["品番", "品名", "標準単価"])

@st.cache_data(ttl=60)
def load_customer_master():
    """顧客マスタCSVの読み込み"""
    if not os.path.exists(CUSTOMERS_CSV):
        # 初回サンプルデータの自動生成
        sample_cust = pd.DataFrame([
            {"顧客名": "株式会社サンプル商事 本社", "電話番号": "03-1234-5678", "住所": "東京都千代田区丸の内1-1-1"},
            {"顧客名": "株式会社サンプル商事 大阪支店", "電話番号": "06-9876-5432", "住所": "大阪府大阪市北区梅田2-2-2"},
            {"顧客名": "東京リユース工業", "電話番号": "03-3333-4444", "住所": "東京都大田区羽田旭町3-3"},
            {"顧客名": "埼玉メンテナンス", "電話番号": "048-555-6666", "住所": "埼玉県さいたま市大宮区桜木町4-4"},
        ])
        sample_cust.to_csv(CUSTOMERS_CSV, index=False, encoding="utf-8-sig")
        return sample_cust
    try:
        return pd.read_csv(CUSTOMERS_CSV, encoding="utf-8-sig", dtype={"電話番号": str})
    except Exception:
        return pd.DataFrame(columns=["顧客名", "電話番号", "住所"])

# CSV安全読み込み関数
def load_orders_safe():
    if not os.path.exists(ORDERS_CSV):
        return pd.DataFrame(columns=COLUMNS)
    try:
        df = pd.read_csv(ORDERS_CSV, encoding="utf-8-sig", dtype={"電話番号": str, "品番": str})
        if list(df.columns) != COLUMNS:
            df = pd.DataFrame(columns=COLUMNS)
            df.to_csv(ORDERS_CSV, index=False, encoding="utf-8-sig")
        return df
    except Exception:
        df = pd.DataFrame(columns=COLUMNS)
        df.to_csv(ORDERS_CSV, index=False, encoding="utf-8-sig")
        return df

# Excel生成関数
def to_excel_bytes(df):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='受注データ一覧')
    return output.getvalue()

# 注文保存用関数
def save_order_items(channel, customer, tel, address, delivery_date, items, notes):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    new_rows = []
    for itm in items:
        qty = int(itm.get("qty", itm.get("quantity", 1)))
        price = int(itm.get("price", 0))
        subtotal = qty * price
        new_rows.append({
            "注文日時": now_str,
            "受付種別": channel,
            "顧客名": customer,
            "電話番号": str(tel),
            "住所": address,
            "品番": itm.get("code", itm.get("item_code", "-")),
            "品名": itm.get("name", itm.get("item_name", "")),
            "数量": qty,
            "単価": price,
            "小計": subtotal,
            "希望納期": str(delivery_date),
            "備考": notes
        })
    df_new = pd.DataFrame(new_rows)
    df_existing = load_orders_safe()
    df_combined = pd.concat([df_existing, df_new], ignore_index=True)
    df_combined.to_csv(ORDERS_CSV, index=False, encoding="utf-8-sig")

# APIキー設定
GEMINI_API_KEY = st.secrets.get("GEMINI_API_KEY", "YOUR_GEMINI_API_KEY")
if GEMINI_API_KEY != "YOUR_GEMINI_API_KEY":
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-1.5-flash")

# セッション状態の初期化
if "current_order" not in st.session_state:
    st.session_state.current_order = None
if "phone_cart" not in st.session_state:
    st.session_state.phone_cart = []

def extract_order_info(input_data, is_image=False):
    system_prompt = """
    あなたは受発注伝票の解析アシスタントです。
    入力された情報（注文書画像またはメール本文）から、以下の情報を抽出し、必ずJSON形式のみで出力してください。
    【出力フォーマット】
    {
      "customer_name": "顧客名・会社名（不明なら空文字）",
      "customer_tel": "電話番号（不明なら空文字）",
      "customer_address": "納品先住所または会社住所（不明なら空文字）",
      "items": [{"item_name": "商品名や品番", "quantity": 数値, "unit": "個/本など"}],
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

df_items_master = load_item_master()
df_cust_master = load_customer_master()

tab_fax, tab_mail, tab_phone, tab_list = st.tabs([
    "📠 FAX（写真・スキャン）", 
    "✉️ メール（コピペ解析）", 
    "📞 電話（顧客・商品検索）", 
    "📋 登録済み注文一覧"
])

# ==========================================
# 1. FAX
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
                with st.spinner("Geminiが解析中..."):
                    result = extract_order_info(img, is_image=True)
                    if result:
                        st.session_state.current_order = result
                        st.success("解析完了！右側で内容を確認してください。")

    with col2:
        st.subheader("📝 読取結果の確認・修正")
        if st.session_state.current_order:
            order = st.session_state.current_order
            c_name = st.text_input("顧客名・会社名", value=order.get("customer_name", ""), key="fax_cname")
            col_f1, col_f2 = st.columns(2)
            c_tel = col_f1.text_input("電話番号", value=order.get("customer_tel", ""), key="fax_tel")
            d_date = col_f2.text_input("希望納期", value=order.get("delivery_date", ""), key="fax_ddate")
            c_addr = st.text_input("住所・納品先", value=order.get("customer_address", ""), key="fax_addr")
            
            st.write("▼ 注文明細")
            items = order.get("items", [])
            fax_items_to_save = []
            for i, itm in enumerate(items):
                c_i1, c_i2, c_i3 = st.columns([3, 1, 1])
                name = c_i1.text_input(f"品名/品番 #{i+1}", value=itm.get("item_name", ""), key=f"fax_item_{i}")
                qty = c_i2.number_input(f"数量 #{i+1}", value=int(itm.get("quantity", 1)), min_value=1, key=f"fax_qty_{i}")
                unit = c_i3.text_input(f"単位 #{i+1}", value=itm.get("unit", "個"), key=f"fax_unit_{i}")
                fax_items_to_save.append({"name": name, "qty": qty, "price": 0})
            
            notes = st.text_area("備考", value=order.get("notes", ""), key="fax_notes")
            if st.button("✅ この内容で注文を確定・保存", key="btn_save_fax", type="primary"):
                save_order_items("FAX", c_name, c_tel, c_addr, d_date, fax_items_to_save, notes)
                st.success("FAX注文を保存しました！一覧タブを確認してください。")
                st.session_state.current_order = None

# ==========================================
# 2. メール
# ==========================================
with tab_mail:
    st.subheader("メール本文のコピペ解析")
    mail_text = st.text_area("メール本文を貼り付け", height=120, placeholder="〇〇商事です。高圧ホース3mを2本送ってください。")
    if st.button("🤖 メールから注文内容を抽出", key="btn_mail_ai"):
        if mail_text:
            with st.spinner("AI解析中..."):
                result = extract_order_info(mail_text, is_image=False)
                if result:
                    st.session_state.current_order = result
                    st.json(result)

# ==========================================
# 3. 電話（インクリメンタル部分一致検索対応）
# ==========================================
with tab_phone:
    st.subheader("📞 電話受付 - 部分一致検索と明細登録")
    
    st.markdown("#### 1. 顧客の検索・選択")
    col_cs1, col_cs2 = st.columns([2, 1])
    cust_query = col_cs1.text_input("🔍 顧客検索（社名・電話・住所の一部を入力）", placeholder="例: 大阪、サンプル、03、大田区 など", key="cust_search_query")
    req_date = col_cs2.date_input("希望納期", key="phone_date")

    # 顧客の部分一致フィルタリング
    if cust_query:
        matched_cust = df_cust_master[
            df_cust_master["顧客名"].str.contains(cust_query, case=False, na=False) |
            df_cust_master["電話番号"].astype(str).str.contains(cust_query, case=False, na=False) |
            df_cust_master["住所"].str.contains(cust_query, case=False, na=False)
        ]
    else:
        matched_cust = df_cust_master

    cust_options = ["新規または手入力"] + [
        f"{row['顧客名']} ｜ TEL: {row['電話番号']} ｜ {row['住所']}"
        for _, row in matched_cust.iterrows()
    ]

    selected_cust_str = st.selectbox(
        f"検索結果候補（{len(matched_cust)} 件該当）", 
        cust_options, 
        key="selected_cust_dropdown"
    )

    # 選択した顧客情報を各入力欄の初期値に自動反映
    if selected_cust_str != "新規または手入力":
        selected_cust_name = selected_cust_str.split(" ｜ ")[0]
        row_match = df_cust_master[df_cust_master["顧客名"] == selected_cust_name].iloc[0]
        init_name = row_match["顧客名"]
        init_tel = str(row_match["電話番号"])
        init_addr = str(row_match["住所"])
    else:
        init_name = ""
        init_tel = ""
        init_addr = ""

    col_info1, col_info2, col_info3 = st.columns([2, 2, 3])
    cust_name_final = col_info1.text_input("顧客名（確定・編集可）", value=init_name, key="phone_cust_name_final")
    cust_tel_final = col_info2.text_input("電話番号", value=init_tel, key="phone_tel_final")
    cust_addr_final = col_info3.text_input("住所・納品先", value=init_addr, key="phone_addr_final")

    st.markdown("---")
    st.markdown("#### 2. 商品の検索・カート追加")
    
    col_is1, col_is2 = st.columns([2, 3])
    item_query = col_is1.text_input("🔍 商品検索（品番・品名の一部を入力）", placeholder="例: ホース、ノズル、A-10 など", key="item_search_query")
    
    # 商品の部分一致フィルタリング
    if item_query:
        matched_items = df_items_master[
            df_items_master["品番"].astype(str).str.contains(item_query, case=False, na=False) |
            df_items_master["品名"].str.contains(item_query, case=False, na=False)
        ]
    else:
        matched_items = df_items_master

    item_options = [
        f"【{row['品番']}】 {row['品名']} (定価: ¥{int(row['標準単価']):,})"
        for _, row in matched_items.iterrows()
    ]

    if item_options:
        selected_item_str = col_is2.selectbox(
            f"商品候補（{len(matched_items)} 件該当）", 
            item_options, 
            key="selected_item_dropdown"
        )
        
        # 選択された商品情報から品番を特定
        selected_code = selected_item_str.split("】")[0].replace("【", "").strip()
        item_row = df_items_master[df_items_master["品番"].astype(str) == selected_code].iloc[0]
        default_price = int(item_row["標準単価"])
        selected_name = str(item_row["品名"])

        col_p1, col_p2, col_p3 = st.columns([1, 1, 1])
        qty = col_p1.number_input("数量", min_value=1, value=1, key="add_qty")
        unit_price = col_p2.number_input("単価（円）", value=default_price, step=100, key="add_price")
        
        with col_p3:
            st.write("")
            st.write("")
            if st.button("＋ 明細に追加", use_container_width=True):
                st.session_state.phone_cart.append({
                    "code": selected_code,
                    "name": selected_name,
                    "qty": qty,
                    "price": unit_price,
                    "subtotal": qty * unit_price
                })
                st.rerun()
    else:
        st.warning("一致する商品が見つかりません。検索ワードを変更してください。")

    # カート明細一覧
    if st.session_state.phone_cart:
        st.write("### 📋 注文明細一覧")
        cart_df = pd.DataFrame(st.session_state.phone_cart)
        st.dataframe(cart_df.rename(columns={"code": "品番", "name": "品名", "qty": "数量", "price": "単価(円)", "subtotal": "小計(円)"}), use_container_width=True)
        total_amount = sum(item["subtotal"] for item in st.session_state.phone_cart)
        st.markdown(f"#### 💰 合計金額: **¥{total_amount:,}**（税別）")
        
        col_act1, col_act2 = st.columns([1, 4])
        if col_act1.button("🗑️ 明細をクリア"):
            st.session_state.phone_cart = []
            st.rerun()
            
        phone_notes = st.text_input("通話メモ・特記事項", key="phone_note_input")
        if st.button("✅ 複数商品の電話注文を確定・保存", type="primary", use_container_width=True):
            if not cust_name_final:
                st.error("顧客名を入力してください。")
            else:
                save_order_items("電話", cust_name_final, cust_tel_final, cust_addr_final, req_date, st.session_state.phone_cart, phone_notes)
                st.success(f"【登録完了】{cust_name_final} 様の注文を保存しました！")
                st.session_state.phone_cart = []
                st.rerun()
    else:
        st.info("商品を選択して「＋ 明細に追加」を押してください。")

# ==========================================
# 4. 登録済み一覧
# ==========================================
with tab_list:
    st.subheader("📋 登録済み注文一覧（最新データ）")
    
    df_orders = load_orders_safe()
    
    if not df_orders.empty:
        st.dataframe(df_orders, use_container_width=True)
        
        col_d1, col_d2, col_d3 = st.columns([2, 2, 1])
        
        excel_bytes = to_excel_bytes(df_orders)
        col_d1.download_button(
            label="📊 Excelファイル（.xlsx）を出力",
            data=excel_bytes,
            file_name=f"orders_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary"
        )
        
        csv_data = df_orders.to_csv(index=False, encoding="utf_8_sig").encode("utf_8_sig")
        col_d2.download_button(
            label="📥 連携用CSVファイルを出力",
            data=csv_data,
            file_name=f"orders_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
            mime="text/csv",
        )
        
        if col_d3.button("🗑️ 全件クリア"):
            if os.path.exists(ORDERS_CSV):
                os.remove(ORDERS_CSV)
            st.rerun()
    else:
        st.info("まだ登録された注文データはありません。")
