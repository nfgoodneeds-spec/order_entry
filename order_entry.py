import streamlit as st
import pandas as pd
import json
import os
import io
import re
from datetime import datetime
import google.generativeai as genai
from PIL import Image

# --------------------------------------------------
# 初期設定 & サイドバー設定
# --------------------------------------------------
st.set_page_config(page_title="受発注DXタブレットアプリ", layout="wide")

ORDERS_CSV = "orders_data.csv"
ITEMS_CSV = "items.csv"
CUSTOMERS_CSV = "customers.csv"

COLUMNS = [
    "注文日時", "受付種別", "顧客コード", "顧客名", "郵便番号", 
    "住所", "電話番号", "品番", "品名", "数量", "単位", "単価", "小計", "希望納期", "備考"
]

with st.sidebar:
    st.header("⚙️ 設定")
    secret_key = st.secrets.get("GEMINI_API_KEY", "")
    api_key_input = st.text_input(
        "Gemini API Key", 
        value=secret_key, 
        type="password",
        placeholder="AI Studioで取得したAPIキーを入力"
    )

# --------------------------------------------------
# マスタデータの読み込み（文字コード自動判別・基幹ヘッダ完全対応）
# --------------------------------------------------
@st.cache_data(ttl=10)
def load_customer_master():
    """顧客マスタCSVの読み込み"""
    if not os.path.exists(CUSTOMERS_CSV):
        return pd.DataFrame([
            {"顧客コード": "1", "顧客名": "株式会社サンプル商事 本社", "郵便番号": "100-0005", "住所": "東京都千代田区丸の内1-1-1", "電話番号": "03-1234-5678"},
            {"顧客コード": "2", "顧客名": "株式会社サンプル商事 大阪支店", "郵便番号": "530-0001", "住所": "大阪府大阪市北区梅田2-2-2", "電話番号": "06-9876-5432"},
        ])

    df_raw = None
    for enc in ["cp932", "shift_jis", "utf-8-sig", "utf-8"]:
        try:
            df_raw = pd.read_csv(CUSTOMERS_CSV, header=None, dtype=str, encoding=enc)
            break
        except Exception:
            continue

    if df_raw is None:
        return pd.DataFrame(columns=["顧客コード", "顧客名", "郵便番号", "住所", "電話番号"])

    try:
        header_row_idx = 0
        for idx, row in df_raw.head(10).iterrows():
            row_str = " ".join(row.dropna().astype(str))
            if "得意先ｺｰﾄﾞ" in row_str or "得意先コード" in row_str or "得意先名称" in row_str or "電話番号" in row_str:
                header_row_idx = idx
                break
        
        header_cols = [str(x).strip() for x in df_raw.iloc[header_row_idx].fillna("").tolist()]
        df = df_raw.iloc[header_row_idx + 1:].copy()
        df.columns = header_cols

        code_col = next((c for c in df.columns if "ｺｰﾄﾞ" in c or "コード" in c), df.columns[0])
        name_col = next((c for c in df.columns if "名称" in c or "名" in c), df.columns[1])
        zip_col = next((c for c in df.columns if "郵便" in c), None)
        tel_col = next((c for c in df.columns if "電話" in c or "TEL" in c), None)
        addr_cols = [c for c in df.columns if "住所" in c]
        
        result_df = pd.DataFrame()
        result_df["顧客コード"] = df[code_col].fillna("").astype(str).str.strip()
        result_df["顧客名"] = df[name_col].fillna("").astype(str).str.strip()
        result_df["郵便番号"] = df[zip_col].fillna("").astype(str).str.strip() if zip_col else ""
        result_df["電話番号"] = df[tel_col].fillna("").astype(str).str.strip() if tel_col else ""
        
        if addr_cols:
            result_df["住所"] = df[addr_cols].fillna("").apply(
                lambda r: " ".join([str(x).strip() for x in r if str(x).strip() and str(x) != "nan"]), 
                axis=1
            )
        else:
            result_df["住所"] = ""
            
        result_df = result_df[result_df["顧客名"] != ""]
        result_df = result_df[~result_df["顧客名"].str.contains("名称|得意先", na=False)]
        return result_df.reset_index(drop=True)
    except Exception as e:
        st.warning(f"顧客マスタ処理注記: {e}")
        return pd.DataFrame(columns=["顧客コード", "顧客名", "郵便番号", "住所", "電話番号"])

@st.cache_data(ttl=10)
def load_item_master():
    """商品マスタCSVの読み込み（基幹10,000件対応・単価/単位自動取得）"""
    if not os.path.exists(ITEMS_CSV):
        return pd.DataFrame([
            {"品番": "500102", "品名": "溶剤 AK-35", "品名索引": "AK-35", "単位": "缶", "標準単価": 17500},
            {"品番": "100002", "品名": "BL-10", "品名索引": "BL-10", "単位": "台", "標準単価": 45000},
        ])

    df = None
    for enc in ["cp932", "shift_jis", "utf-8-sig", "utf-8"]:
        try:
            df = pd.read_csv(ITEMS_CSV, dtype=str, encoding=enc)
            break
        except Exception:
            continue

    if df is None:
        return pd.DataFrame(columns=["品番", "品名", "品名索引", "単位", "標準単価"])

    try:
        code_col = next((c for c in df.columns if "ｺｰﾄﾞ" in c or "コード" in c or "品番" in c), df.columns[0])
        name_col = next((c for c in df.columns if ("商品名" in c or "品名" in c) and "索引" not in c), df.columns[1])
        index_col = next((c for c in df.columns if "索引" in c or "略称" in c), None)
        unit_col = next((c for c in df.columns if "単位" in c), None)
        price_col = next((c for c in df.columns if "単価" in c or "価格" in c or "上代" in c), None)

        res_df = pd.DataFrame()
        res_df["品番"] = df[code_col].fillna("").astype(str).str.strip()
        res_df["品名"] = df[name_col].fillna("").astype(str).str.strip()
        res_df["品名索引"] = df[index_col].fillna("").astype(str).str.strip() if index_col else ""
        res_df["単位"] = df[unit_col].fillna("個").astype(str).str.strip() if unit_col else "個"
        
        # 単価の数値整形
        if price_col:
            res_df["標準単価"] = pd.to_numeric(df[price_col].astype(str).str.replace(",", ""), errors='coerce').fillna(0).astype(int)
        else:
            res_df["標準単価"] = 0

        res_df = res_df[res_df["品名"] != ""]
        return res_df.reset_index(drop=True)
    except Exception as e:
        st.warning(f"商品マスタ処理注記: {e}")
        return pd.DataFrame(columns=["品番", "品名", "品名索引", "単位", "標準単価"])

def load_orders_safe():
    if not os.path.exists(ORDERS_CSV):
        return pd.DataFrame(columns=COLUMNS)
    try:
        df = pd.read_csv(ORDERS_CSV, encoding="utf-8-sig", dtype=str)
        if list(df.columns) != COLUMNS:
            df = pd.DataFrame(columns=COLUMNS)
            df.to_csv(ORDERS_CSV, index=False, encoding="utf-8-sig")
        return df
    except Exception:
        df = pd.DataFrame(columns=COLUMNS)
        df.to_csv(ORDERS_CSV, index=False, encoding="utf-8-sig")
        return df

def to_excel_bytes(df):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='受注データ一覧')
    return output.getvalue()

def save_order_items(channel, code, customer, zip_code, tel, address, delivery_date, items, notes):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    new_rows = []
    for itm in items:
        try:
            qty = int(itm.get("qty", itm.get("quantity", 1)))
        except Exception:
            qty = 1
        try:
            price = int(itm.get("price", 0))
        except Exception:
            price = 0
        subtotal = qty * price
        new_rows.append({
            "注文日時": now_str,
            "受付種別": channel,
            "顧客コード": str(code),
            "顧客名": customer,
            "郵便番号": str(zip_code),
            "住所": address,
            "電話番号": str(tel),
            "品番": itm.get("code", itm.get("item_code", "-")),
            "品名": itm.get("name", itm.get("item_name", "")),
            "数量": qty,
            "単位": itm.get("unit", "個"),
            "単価": price,
            "小計": subtotal,
            "希望納期": str(delivery_date),
            "備考": notes
        })
    df_new = pd.DataFrame(new_rows)
    df_existing = load_orders_safe()
    df_combined = pd.concat([df_existing, df_new], ignore_index=True)
    df_combined.to_csv(ORDERS_CSV, index=False, encoding="utf-8-sig")

# --------------------------------------------------
# セッション状態初期化
# --------------------------------------------------
if "current_order_fax" not in st.session_state:
    st.session_state.current_order_fax = None
if "current_order_mail" not in st.session_state:
    st.session_state.current_order_mail = None
if "phone_cart" not in st.session_state:
    st.session_state.phone_cart = []

for k in ["phone_cust_code_final", "phone_cust_name_final", "phone_zip_final", "phone_tel_final", "phone_addr_final"]:
    if k not in st.session_state:
        st.session_state[k] = ""

def on_customer_selected():
    sel_val = st.session_state.get("selected_cust_dropdown", "")
    df_cust = load_customer_master()
    if sel_val and sel_val != "新規または手入力":
        sel_code = sel_val.split("】")[0].replace("【", "").strip()
        matched = df_cust[df_cust["顧客コード"] == sel_code]
        if not matched.empty:
            row = matched.iloc[0]
            st.session_state["phone_cust_code_final"] = str(row["顧客コード"])
            st.session_state["phone_cust_name_final"] = str(row["顧客名"])
            st.session_state["phone_zip_final"] = str(row["郵便番号"])
            st.session_state["phone_tel_final"] = str(row["電話番号"])
            st.session_state["phone_addr_final"] = str(row["住所"])
    elif sel_val == "新規または手入力":
        st.session_state["phone_cust_code_final"] = ""
        st.session_state["phone_cust_name_final"] = ""
        st.session_state["phone_zip_final"] = ""
        st.session_state["phone_tel_final"] = ""
        st.session_state["phone_addr_final"] = ""

def extract_order_info(input_data, is_image=False):
    if not api_key_input:
        st.error("左側サイドバーにGemini APIキーを入力してください。")
        return None
        
    try:
        genai.configure(api_key=api_key_input.strip())
    except Exception as e:
        st.error(f"APIキー設定エラー: {e}")
        return None

    system_prompt = """
    あなたは受発注伝票の解析アシスタントです。
    入力された情報（注文書画像またはメール本文）から、以下の情報を抽出し、必ずJSON形式のみで出力してください。
    余分な解説やMarkdownフォーマットは含めず、純粋なJSONテキストのみを返してください。

    【出力フォーマット】
    {
      "customer_code": "顧客コード（不明なら空文字）",
      "customer_name": "顧客名・会社名（不明なら空文字）",
      "customer_zip": "郵便番号（不明なら空文字）",
      "customer_tel": "電話番号（不明なら空文字）",
      "customer_address": "住所（不明なら空文字）",
      "items": [
        {"item_name": "商品名や品番", "quantity": 1, "unit": "個/本/缶など"}
      ],
      "delivery_date": "希望納期（YYYY-MM-DD形式、不明なら空文字）",
      "notes": "特記事項・署名・役職など"
    }
    """
    
    valid_models = []
    try:
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                valid_models.append(m.name)
    except Exception:
        pass

    if not valid_models:
        valid_models = ["models/gemini-1.5-flash", "models/gemini-2.0-flash", "models/gemini-1.5-pro"]

    last_error = None
    for model_name in valid_models:
        try:
            m = genai.GenerativeModel(model_name)
            if is_image:
                response = m.generate_content([system_prompt, input_data])
            else:
                response = m.generate_content(f"{system_prompt}\n\n【対象テキスト】\n{input_data}")
            
            raw_text = response.text.strip()
            clean_text = re.sub(r"```json\s*", "", raw_text)
            clean_text = re.sub(r"```\s*", "", clean_text).strip()
            return json.loads(clean_text)
        except Exception as e:
            last_error = e
            continue

    st.error(f"AI解析エラー: {last_error}")
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

# 1. FAX
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
                        st.session_state.current_order_fax = result
                        st.success("解析完了！右側で内容を確認してください。")

    with col2:
        st.subheader("📝 読取結果の確認・修正")
        if st.session_state.current_order_fax:
            order = st.session_state.current_order_fax
            col_fc1, col_fc2 = st.columns([1, 2])
            c_code = col_fc1.text_input("顧客コード", value=order.get("customer_code", ""), key="fax_ccode")
            c_name = col_fc2.text_input("顧客名・会社名", value=order.get("customer_name", ""), key="fax_cname")
            
            col_f1, col_f2, col_f3 = st.columns([1, 1, 1])
            c_zip = col_f1.text_input("郵便番号", value=order.get("customer_zip", ""), key="fax_zip")
            c_tel = col_f2.text_input("電話番号", value=order.get("customer_tel", ""), key="fax_tel")
            d_date = col_f3.text_input("希望納期", value=order.get("delivery_date", ""), key="fax_ddate")
            
            c_addr = st.text_input("住所・納品先", value=order.get("customer_address", ""), key="fax_addr")
            
            st.write("▼ 注文明細")
            items = order.get("items", [])
            fax_items_to_save = []
            for i, itm in enumerate(items):
                c_i1, c_i2, c_i3 = st.columns([3, 1, 1])
                name = c_i1.text_input(f"品名/品番 #{i+1}", value=itm.get("item_name", ""), key=f"fax_item_{i}")
                qty = c_i2.number_input(f"数量 #{i+1}", value=int(itm.get("quantity", 1)), min_value=1, key=f"fax_qty_{i}")
                unit = c_i3.text_input(f"単位 #{i+1}", value=itm.get("unit", "個"), key=f"fax_unit_{i}")
                fax_items_to_save.append({"name": name, "qty": qty, "unit": unit, "price": 0})
            
            notes = st.text_area("備考", value=order.get("notes", ""), key="fax_notes")
            if st.button("✅ この内容で注文を確定・保存", key="btn_save_fax", type="primary"):
                save_order_items("FAX", c_code, c_name, c_zip, c_tel, c_addr, d_date, fax_items_to_save, notes)
                st.success("FAX注文を保存しました！「登録済み注文一覧」タブを確認してください。")
                st.session_state.current_order_fax = None

# 2. メール
with tab_mail:
    st.subheader("メール本文のコピペ解析 ＆ 登録")
    col_m1, col_m2 = st.columns([1, 1])
    
    with col_m1:
        mail_text = st.text_area(
            "メール本文を貼り付け", 
            height=200, 
            placeholder="お世話様です。\n溶剤 AK-35 2缶 注文をお願いします。\n\n株式会社 ケーアイ CSO\n藤田 信昭\n〒910-0367 福井県坂井市丸岡町羽崎12-16-19\nTEL: 0776-67-1777"
        )
        if st.button("🤖 メールから注文内容を抽出", key="btn_mail_ai"):
            if mail_text:
                with st.spinner("Geminiが解析中..."):
                    result = extract_order_info(mail_text, is_image=False)
                    if result:
                        st.session_state.current_order_mail = result
                        st.success("抽出完了！右側で内容を確認・修正してください。")

    with col_m2:
        st.subheader("📝 抽出結果の確認・修正")
        if st.session_state.current_order_mail:
            m_order = st.session_state.current_order_mail
            col_mc1, col_mc2 = st.columns([1, 2])
            m_code = col_mc1.text_input("顧客コード", value=m_order.get("customer_code", ""), key="mail_ccode")
            m_name = col_mc2.text_input("顧客名・会社名", value=m_order.get("customer_name", ""), key="mail_cname")
            
            col_mf1, col_mf2, col_mf3 = st.columns([1, 1, 1])
            m_zip = col_mf1.text_input("郵便番号", value=m_order.get("customer_zip", ""), key="mail_zip")
            m_tel = col_mf2.text_input("電話番号", value=m_order.get("customer_tel", ""), key="mail_tel")
            m_ddate = col_mf3.text_input("希望納期", value=m_order.get("delivery_date", ""), key="mail_ddate")
            
            m_addr = st.text_input("住所・納品先", value=m_order.get("customer_address", ""), key="mail_addr")
            
            st.write("▼ 注文明細")
            m_items = m_order.get("items", [])
            mail_items_to_save = []
            for i, itm in enumerate(m_items):
                c_i1, c_i2, c_i3 = st.columns([3, 1, 1])
                name = c_i1.text_input(f"品名/品番 #{i+1}", value=itm.get("item_name", ""), key=f"mail_item_{i}")
                qty = c_i2.number_input(f"数量 #{i+1}", value=int(itm.get("quantity", 1)), min_value=1, key=f"mail_qty_{i}")
                unit = c_i3.text_input(f"単位 #{i+1}", value=itm.get("unit", "個"), key=f"mail_unit_{i}")
                mail_items_to_save.append({"name": name, "qty": qty, "unit": unit, "price": 0})
            
            m_notes = st.text_area("備考", value=m_order.get("notes", ""), key="mail_notes")
            if st.button("✅ この内容でメール注文を確定・保存", key="btn_save_mail", type="primary"):
                save_order_items("メール", m_code, m_name, m_zip, m_tel, m_addr, m_ddate, mail_items_to_save, m_notes)
                st.success("メール注文を保存しました！「登録済み注文一覧」タブを確認してください。")
                st.session_state.current_order_mail = None
                st.rerun()

# 3. 電話（顧客・商品マスタ1万件完全連動）
with tab_phone:
    st.subheader("📞 電話受付 - 顧客・商品検索と明細登録")
    col_stat1, col_stat2 = st.columns(2)
    col_stat1.caption(f"※ 顧客マスタ: **{len(df_cust_master):,}** 件")
    col_stat2.caption(f"※ 商品マスタ: **{len(df_items_master):,}** 件")
    
    st.markdown("#### 1. 顧客の検索・選択")
    col_cs1, col_cs2 = st.columns([2, 1])
    cust_query = col_cs1.text_input("🔍 顧客検索（コード・社名・郵便番号・電話・住所の一部を入力）", placeholder="例: 創美、アタゴ、910、0776、館林市 など", key="cust_search_query")
    req_date = col_cs2.date_input("希望納期", key="phone_date")

    if cust_query:
        matched_cust = df_cust_master[
            df_cust_master["顧客コード"].str.contains(cust_query, case=False, na=False) |
            df_cust_master["顧客名"].str.contains(cust_query, case=False, na=False) |
            df_cust_master["郵便番号"].str.contains(cust_query, case=False, na=False) |
            df_cust_master["電話番号"].str.contains(cust_query, case=False, na=False) |
            df_cust_master["住所"].str.contains(cust_query, case=False, na=False)
        ]
    else:
        matched_cust = df_cust_master.head(50)

    cust_options = ["新規または手入力"] + [
        f"【{row['顧客コード']}】 {row['顧客名']} ｜ 〒{row['郵便番号']} ｜ {row['住所']} ｜ TEL: {row['電話番号']}"
        for _, row in matched_cust.iterrows()
    ]

    st.selectbox(
        f"検索結果候補（該当 {len(matched_cust)} 件）", 
        cust_options, 
        key="selected_cust_dropdown",
        on_change=on_customer_selected
    )

    col_info1, col_info2 = st.columns([1, 3])
    cust_code_final = col_info1.text_input("顧客コード", key="phone_cust_code_final")
    cust_name_final = col_info2.text_input("顧客名（確定・編集可）", key="phone_cust_name_final")
    
    col_info3, col_info4, col_info5 = st.columns([1, 1, 2])
    cust_zip_final = col_info3.text_input("郵便番号", key="phone_zip_final")
    cust_tel_final = col_info4.text_input("電話番号", key="phone_tel_final")
    cust_addr_final = col_info5.text_input("住所・納品先", key="phone_addr_final")

    st.markdown("---")
    st.markdown("#### 2. 商品の検索・カート追加")
    
    col_is1, col_is2 = st.columns([2, 3])
    item_query = col_is1.text_input("🔍 商品検索（コード・商品名・略称の一部を入力）", placeholder="例: AK-35、BL-10、ノズル、ホース など", key="item_search_query")
    
    if item_query:
        matched_items = df_items_master[
            df_items_master["品番"].astype(str).str.contains(item_query, case=False, na=False) |
            df_items_master["品名"].str.contains(item_query, case=False, na=False) |
            df_items_master["品名索引"].str.contains(item_query, case=False, na=False)
        ]
    else:
        matched_items = df_items_master.head(50)

    item_options = [
        f"【{row['品番']}】 {row['品名']} ｜ 単位: {row['単位']} ｜ 上代: ¥{int(row['標準単価']):,}"
        for _, row in matched_items.iterrows()
    ]

    if item_options:
        selected_item_str = col_is2.selectbox(f"商品候補（該当 {len(matched_items)} 件）", item_options, key="selected_item_dropdown")
        selected_code = selected_item_str.split("】")[0].replace("【", "").strip()
        item_row = df_items_master[df_items_master["品番"].astype(str) == selected_code].iloc[0]
        default_price = int(item_row["標準単価"])
        selected_name = str(item_row["品名"])
        selected_unit = str(item_row["単位"])

        col_p1, col_p2, col_p3, col_p4 = st.columns([1, 1, 1, 1])
        qty = col_p1.number_input("数量", min_value=1, value=1, key="add_qty")
        item_unit_input = col_p2.text_input("単位", value=selected_unit, key="add_unit")
        unit_price = col_p3.number_input("単価（円）", value=default_price, step=100, key="add_price")
        
        with col_p4:
            st.write("")
            st.write("")
            if st.button("＋ 明細に追加", use_container_width=True):
                st.session_state.phone_cart.append({
                    "code": selected_code,
                    "name": selected_name,
                    "qty": qty,
                    "unit": item_unit_input,
                    "price": unit_price,
                    "subtotal": qty * unit_price
                })
                st.rerun()
    else:
        st.warning("一致する商品が見つかりません。")

    if st.session_state.phone_cart:
        st.write("### 📋 注文明細一覧")
        cart_df = pd.DataFrame(st.session_state.phone_cart)
        st.dataframe(cart_df.rename(columns={"code": "品番", "name": "品名", "qty": "数量", "unit": "単位", "price": "単価(円)", "subtotal": "小計(円)"}), use_container_width=True)
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
                save_order_items(
                    "電話", cust_code_final, cust_name_final, cust_zip_final, 
                    cust_tel_final, cust_addr_final, req_date, st.session_state.phone_cart, phone_notes
                )
                st.success(f"【登録完了】{cust_name_final} 様（コード: {cust_code_final}）の注文を保存しました！")
                st.session_state.phone_cart = []
                st.rerun()
    else:
        st.info("商品を選択して「＋ 明細に追加」を押してください。")

# 4. 登録済み一覧
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
