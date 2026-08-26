import streamlit as st
import pandas as pd
import json
import os
import io
import re
import unicodedata
from datetime import datetime
import google.generativeai as genai
from PIL import Image, ImageEnhance, ImageOps

# --------------------------------------------------
# 初期設定（全画面レイアウト）
# --------------------------------------------------
st.set_page_config(
    page_title="受発注DXタブレットアプリ", 
    layout="wide",
    initial_sidebar_state="collapsed"
)

ORDERS_CSV = "orders_data.csv"
ITEMS_CSV = "items.csv"
CUSTOMERS_CSV = "customers.csv"

COLUMNS = [
    "注文日時", "受付種別", "顧客コード", "顧客名", "郵便番号", 
    "住所", "電話番号", "品番", "品名", "数量", "単位", "単価", "小計", "希望納期", "備考"
]

API_KEY = st.secrets.get("GEMINI_API_KEY", os.environ.get("GEMINI_API_KEY", ""))

def normalize_text(text):
    if text is None:
        return ""
    return unicodedata.normalize('NFKC', str(text)).lower().strip()

# --------------------------------------------------
# 画像前処理（自動回転・コントラスト・鮮明化）
# --------------------------------------------------
def preprocess_image_for_ocr(img):
    try:
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass
    
    if img.mode != "RGB":
        img = img.convert("RGB")
    
    enhancer_con = ImageEnhance.Contrast(img)
    img_enhanced = enhancer_con.enhance(1.4)
    
    enhancer_sha = ImageEnhance.Sharpness(img_enhanced)
    img_enhanced = enhancer_sha.enhance(1.3)
    
    return img_enhanced

# --------------------------------------------------
# マスタデータの読み込み
# --------------------------------------------------
@st.cache_data(ttl=10)
def load_customer_master():
    """顧客マスタCSVの読み込み"""
    if not os.path.exists(CUSTOMERS_CSV):
        sample_df = pd.DataFrame([
            {"顧客コード": "1", "顧客名": "株式会社サンプル商事 本社", "郵便番号": "100-0005", "住所": "東京都千代田区丸の内1-1-1", "電話番号": "03-1234-5678"},
            {"顧客コード": "2", "顧客名": "株式会社サンプル商事 大阪支店", "郵便番号": "530-0001", "住所": "大阪府大阪市北区梅田2-2-2", "電話番号": "06-9876-5432"},
            {"顧客コード": "3", "顧客名": "株式会社サンプル商事 新宿店", "郵便番号": "160-0022", "住所": "東京都新宿区新宿3-1-1", "電話番号": "03-3333-4444"},
        ])
        sample_df["_search_text"] = sample_df.apply(lambda r: " ".join([normalize_text(x) for x in r]), axis=1)
        sample_df["_clean_tel"] = sample_df["電話番号"].apply(lambda x: re.sub(r"\D", "", str(x)))
        sample_df["_norm_name"] = sample_df["顧客名"].apply(normalize_text)
        sample_df["_norm_addr"] = sample_df["住所"].apply(normalize_text)
        return sample_df

    df_raw = None
    for enc in ["cp932", "shift_jis", "utf-8-sig", "utf-8"]:
        try:
            df_raw = pd.read_csv(CUSTOMERS_CSV, header=None, dtype=str, encoding=enc)
            break
        except Exception:
            continue

    if df_raw is None:
        return pd.DataFrame(columns=["顧客コード", "顧客名", "郵便番号", "住所", "電話番号", "_search_text", "_clean_tel", "_norm_name", "_norm_addr"])

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
            
        result_df = result_df[result_df["顧客名"] != ""].reset_index(drop=True)
        result_df = result_df[~result_df["顧客名"].str.contains("名称|得意先", na=False)].reset_index(drop=True)
        
        result_df["_search_text"] = result_df.apply(
            lambda r: f"{normalize_text(r['顧客コード'])} {normalize_text(r['顧客名'])} {normalize_text(r['郵便番号'])} {normalize_text(r['電話番号'])} {normalize_text(r['住所'])}", 
            axis=1
        )
        result_df["_clean_tel"] = result_df["電話番号"].apply(lambda x: re.sub(r"\D", "", str(x)))
        result_df["_norm_name"] = result_df["顧客名"].apply(normalize_text)
        result_df["_norm_addr"] = result_df["住所"].apply(normalize_text)
        return result_df
    except Exception as e:
        st.warning(f"顧客マスタ処理注記: {e}")
        return pd.DataFrame(columns=["顧客コード", "顧客名", "郵便番号", "住所", "電話番号", "_search_text", "_clean_tel", "_norm_name", "_norm_addr"])

@st.cache_data(ttl=10)
def load_item_master():
    """商品マスタCSVの読み込み"""
    if not os.path.exists(ITEMS_CSV):
        sample_df = pd.DataFrame([
            {"品番": "500102", "品名": "溶剤 AK-35", "品名索引": "AK-35", "単位": "缶", "標準単価": 26000},
            {"品番": "100002", "品名": "BL-10", "品名索引": "BL-10", "単位": "台", "標準単価": 50000},
        ])
        sample_df["_search_text"] = sample_df.apply(lambda r: " ".join([normalize_text(x) for x in r]), axis=1)
        return sample_df

    df = None
    for enc in ["cp932", "shift_jis", "utf-8-sig", "utf-8"]:
        try:
            df = pd.read_csv(ITEMS_CSV, dtype=str, encoding=enc)
            break
        except Exception:
            continue

    if df is None:
        return pd.DataFrame(columns=["品番", "品名", "品名索引", "単位", "標準単価", "_search_text"])

    try:
        code_col = next((c for c in df.columns if "ｺｰﾄﾞ" in c or "コード" in c or "品番" in c), df.columns[0])
        name_col = next((c for c in df.columns if ("商品名" in c or "品名" in c) and "索引" not in c), df.columns[1])
        index_col = next((c for c in df.columns if "索引" in c or "略称" in c), None)
        unit_col = next((c for c in df.columns if "単位" in c), None)
        price_col = next((c for c in df.columns if "単価" in c or "価格" in c or "上代" in c or "ﾗﾝｸ" in c or "ランク" in c), None)

        res_df = pd.DataFrame()
        res_df["品番"] = df[code_col].fillna("").astype(str).str.strip()
        res_df["品名"] = df[name_col].fillna("").astype(str).str.strip()
        res_df["品名索引"] = df[index_col].fillna("").astype(str).str.strip() if index_col else ""
        res_df["単位"] = df[unit_col].fillna("個").astype(str).str.strip() if unit_col else "個"
        
        if price_col:
            res_df["標準単価"] = pd.to_numeric(df[price_col].astype(str).str.replace(",", ""), errors='coerce').fillna(0).astype(int)
        else:
            res_df["標準単価"] = 0

        res_df = res_df[res_df["品名"] != ""].reset_index(drop=True)
        res_df["_search_text"] = res_df.apply(
            lambda r: f"{normalize_text(r['品番'])} {normalize_text(r['品名'])} {normalize_text(r['品名索引'])}", 
            axis=1
        )
        return res_df
    except Exception as e:
        st.warning(f"商品マスタ処理注記: {e}")
        return pd.DataFrame(columns=["品番", "品名", "品名索引", "単位", "標準単価", "_search_text"])

# --------------------------------------------------
# 複数支店・同名店舗の全件候補取得
# --------------------------------------------------
def find_customer_candidates(df_cust, extracted_dict):
    """AI抽出情報から関連する全店舗・全支店候補を返す"""
    if df_cust.empty or not extracted_dict:
        return []

    code_raw = normalize_text(extracted_dict.get("customer_code", ""))
    tel_raw = re.sub(r"\D", "", normalize_text(extracted_dict.get("customer_tel", "")))
    name_raw = normalize_text(extracted_dict.get("customer_name", ""))
    # 支店名や株式会社を除去したコア名（例: 「サンプル商事」「創美」）
    core_name = re.sub(r"\(株\)|株|\(有\)|有限会社|株式会社|支店|本店|店|工場|営業所", "", name_raw).strip()

    matched_indices = []

    # 1. 電話番号一致
    if len(tel_raw) >= 6:
        m_tel = df_cust[df_cust["_clean_tel"].str.contains(tel_raw, na=False)]
        matched_indices.extend(m_tel.index.tolist())

    # 2. 顧客コード一致
    if code_raw:
        m_code = df_cust[df_cust["顧客コード"].apply(normalize_text) == code_raw]
        matched_indices.extend(m_code.index.tolist())

    # 3. 社名・コア名一致（全支店を拾い出す）
    search_keywords = [k for k in [name_raw, core_name] if len(k) >= 2]
    for kw in search_keywords:
        m_name = df_cust[df_cust["_norm_name"].str.contains(kw, na=False)]
        matched_indices.extend(m_name.index.tolist())
        # 逆方向
        for idx, r in df_cust.iterrows():
            if len(r["_norm_name"]) >= 2 and (r["_norm_name"] in kw or kw in r["_norm_name"]):
                matched_indices.append(idx)

    # 重複除外して順序保持
    unique_indices = list(dict.fromkeys(matched_indices))
    if unique_indices:
        return df_cust.loc[unique_indices].to_dict("records")
    return []

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

# 電話用
for k in ["phone_cust_code_final", "phone_cust_name_final", "phone_zip_final", "phone_tel_final", "phone_addr_final"]:
    if k not in st.session_state:
        st.session_state[k] = ""

# FAX用
for k in ["fax_ccode", "fax_cname", "fax_zip", "fax_tel", "fax_addr", "fax_ddate", "fax_notes"]:
    if k not in st.session_state:
        st.session_state[k] = ""

# メール用
for k in ["mail_ccode", "mail_cname", "mail_zip", "mail_tel", "mail_addr", "mail_ddate", "mail_notes"]:
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

def on_fax_branch_change():
    """FAX画面で支店プルダウン変更時に各入力欄を即座に上書き"""
    sel_val = st.session_state.get("fax_cand_select", "")
    df_cust = load_customer_master()
    if sel_val:
        sel_code = sel_val.split("】")[0].replace("【", "").strip()
        matched = df_cust[df_cust["顧客コード"] == sel_code]
        if not matched.empty:
            row = matched.iloc[0]
            st.session_state["fax_ccode"] = str(row["顧客コード"])
            st.session_state["fax_cname"] = str(row["顧客名"])
            st.session_state["fax_zip"] = str(row["郵便番号"])
            st.session_state["fax_tel"] = str(row["電話番号"])
            st.session_state["fax_addr"] = str(row["住所"])

def on_mail_branch_change():
    """メール画面で支店プルダウン変更時に各入力欄を即座に上書き"""
    sel_val = st.session_state.get("mail_cand_select", "")
    df_cust = load_customer_master()
    if sel_val:
        sel_code = sel_val.split("】")[0].replace("【", "").strip()
        matched = df_cust[df_cust["顧客コード"] == sel_code]
        if not matched.empty:
            row = matched.iloc[0]
            st.session_state["mail_ccode"] = str(row["顧客コード"])
            st.session_state["mail_cname"] = str(row["顧客名"])
            st.session_state["mail_zip"] = str(row["郵便番号"])
            st.session_state["mail_tel"] = str(row["電話番号"])
            st.session_state["mail_addr"] = str(row["住所"])

# --------------------------------------------------
# AI解析エンジン
# --------------------------------------------------
def extract_order_info(input_data, is_image=False):
    if not API_KEY:
        st.error("APIキーが設定されていません。Streamlit CloudのSecretsに GEMINI_API_KEY を設定してください。")
        return None
        
    try:
        genai.configure(api_key=API_KEY.strip())
    except Exception as e:
        st.error(f"APIキー設定エラー: {e}")
        return None

    system_prompt = """
    あなたは日本の商取引・受発注業務における高度な注文書解析エキスパートです。
    提供された画像（FAX・手書き注文書・写真）またはメールテキストから、注文内容を正確に読み取り、指定のJSON形式のみで出力してください。

    【読み取り時の最重要ルール】
    1. 【顧客情報】
       - 「発注元」「貴社名」「御中」「得意先名」「送付元」「店舗名」などの欄から発注者会社名・支店名を特定してください。
       - 郵便番号、住所、TEL、FAX番号、顧客コードがあれば正確に抽出してください。
    2. 【注文明細（商品・数量・単位）】
       - 表組み（品名、品番、数量、単位、単価）の各行を漏れなく抽出してください。
       - かすれや手書きの崩し文字がある場合、文脈や標準的な商品名（例: AK-35、BL-10、ホース、ノズル、パッキン等）から推測して補正してください。
       - 数量と単位（缶/本/個/台など）に正しく分離してください。
    3. 【希望納期・備考】
       - 納期、至急等の指定があればYYYY-MM-DD（または日付文字列）で抽出してください。
       - 担当者名、特記事項は notes にまとめてください。

    【出力フォーマット（純粋なJSONテキスト）】
    {
      "customer_code": "顧客コード（不明なら空文字）",
      "customer_name": "顧客名・支店名（不明なら空文字）",
      "customer_zip": "郵便番号（不明なら空文字）",
      "customer_tel": "電話番号（不明なら空文字）",
      "customer_address": "住所（不明なら空文字）",
      "items": [
        {"item_name": "品名・品番", "quantity": 1, "unit": "個/本/缶/台など"}
      ],
      "delivery_date": "希望納期（YYYY-MM-DD形式、不明なら空文字）",
      "notes": "特記事項・担当者名・メモなど"
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

    processed_input = input_data
    if is_image and isinstance(input_data, Image.Image):
        processed_input = preprocess_image_for_ocr(input_data)

    last_error = None
    for model_name in valid_models:
        try:
            m = genai.GenerativeModel(model_name)
            if is_image:
                response = m.generate_content([system_prompt, processed_input])
            else:
                response = m.generate_content(f"{system_prompt}\n\n【対象テキスト】\n{input_data}")
            
            raw_text = response.text.strip()
            clean_text = re.sub(r"```json\s*", "", raw_text)
            clean_text = re.sub(r"```\s*", "", clean_text).strip()
            parsed_json = json.loads(clean_text)
            
            # マスタから全支店候補を探索
            df_cust = load_customer_master()
            candidates = find_customer_candidates(df_cust, parsed_json)
            parsed_json["_candidate_list"] = candidates
            
            # 1件以上の候補があれば最有力候補で初期値を自動設定
            if candidates:
                best = candidates[0]
                parsed_json["customer_code"] = str(best["顧客コード"])
                parsed_json["customer_name"] = str(best["顧客名"])
                parsed_json["customer_zip"] = str(best["郵便番号"])
                parsed_json["customer_tel"] = str(best["電話番号"])
                parsed_json["customer_address"] = str(best["住所"])
            
            return parsed_json
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

# ==========================================
# 1. FAX（写真・スキャン ＆ 支店選択連動）
# ==========================================
with tab_fax:
    st.subheader("FAX注文書の写真・スキャン取り込み")
    col1, col2 = st.columns([1, 1])
    with col1:
        uploaded_file = st.file_uploader("注文書画像を選択（またはカメラ撮影）", type=["jpg", "jpeg", "png", "pdf"], key="fax_upload")
        if uploaded_file:
            img = Image.open(uploaded_file)
            st.image(img, caption="アップロード画像", use_container_width=True)
            if st.button("🤖 AIで高精度読取を実行", key="btn_fax_ai", type="primary"):
                with st.spinner("画像の向き補正・コントラスト強調・顧客マスタ全支店照合中..."):
                    result = extract_order_info(img, is_image=True)
                    if result:
                        st.session_state.current_order_fax = result
                        # 入力欄のセッション状態を即座に同期
                        st.session_state["fax_ccode"] = result.get("customer_code", "")
                        st.session_state["fax_cname"] = result.get("customer_name", "")
                        st.session_state["fax_zip"] = result.get("customer_zip", "")
                        st.session_state["fax_tel"] = result.get("customer_tel", "")
                        st.session_state["fax_addr"] = result.get("customer_address", "")
                        st.session_state["fax_ddate"] = result.get("delivery_date", "")
                        st.session_state["fax_notes"] = result.get("notes", "")
                        st.success("解析完了！右側で支店や内容を確認してください。")

    with col2:
        st.subheader("📝 読取結果の確認・修正")
        if st.session_state.current_order_fax:
            order = st.session_state.current_order_fax
            candidates = order.get("_candidate_list", [])
            
            # 支店・店舗の選択プルダウン（同名店舗・複数拠点に対応）
            if candidates:
                cand_options = [
                    f"【{c['顧客コード']}】 {c['顧客名']} ｜ 〒{c['郵便番号']} ｜ {c['住所']} ｜ TEL:{c['電話番号']}"
                    for c in candidates
                ]
                if len(candidates) > 1:
                    st.warning(f"⚠️ 同名・関連する店舗が **{len(candidates)} 件** 見つかりました。該当の支店を選択してください：")
                else:
                    st.success(f"🎯 顧客マスタと一致しました（支店の変更も可能です）：")
                
                st.selectbox(
                    "🏢 登録店舗・支店の切り替え", 
                    cand_options, 
                    key="fax_cand_select",
                    on_change=on_fax_branch_change
                )
            
            col_fc1, col_fc2 = st.columns([1, 2])
            c_code = col_fc1.text_input("顧客コード", key="fax_ccode")
            c_name = col_fc2.text_input("顧客名・会社名", key="fax_cname")
            
            col_f1, col_f2, col_f3 = st.columns([1, 1, 1])
            c_zip = col_f1.text_input("郵便番号", key="fax_zip")
            c_tel = col_f2.text_input("電話番号", key="fax_tel")
            d_date = col_f3.text_input("希望納期", key="fax_ddate")
            
            c_addr = st.text_input("住所・納品先", key="fax_addr")
            
            st.write("▼ 注文明細")
            items = order.get("items", [])
            fax_items_to_save = []
            for i, itm in enumerate(items):
                c_i1, c_i2, c_i3 = st.columns([3, 1, 1])
                name = c_i1.text_input(f"品名/品番 #{i+1}", value=itm.get("item_name", ""), key=f"fax_item_{i}")
                qty = c_i2.number_input(f"数量 #{i+1}", value=int(itm.get("quantity", 1)), min_value=1, key=f"fax_qty_{i}")
                unit = c_i3.text_input(f"単位 #{i+1}", value=itm.get("unit", "個"), key=f"fax_unit_{i}")
                fax_items_to_save.append({"name": name, "qty": qty, "unit": unit, "price": 0})
            
            notes = st.text_area("備考", key="fax_notes")
            if st.button("✅ この内容で注文を確定・保存", key="btn_save_fax", type="primary"):
                save_order_items("FAX", c_code, c_name, c_zip, c_tel, c_addr, d_date, fax_items_to_save, notes)
                st.success("FAX注文を保存しました！「登録済み注文一覧」タブを確認してください。")
                st.session_state.current_order_fax = None
                st.rerun()

# ==========================================
# 2. メール（コピペ解析 ＆ 支店選択連動）
# ==========================================
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
                with st.spinner("Gemini解析 ＆ 顧客マスタ全支店照合中..."):
                    result = extract_order_info(mail_text, is_image=False)
                    if result:
                        st.session_state.current_order_mail = result
                        # 入力欄のセッション状態を即座に同期
                        st.session_state["mail_ccode"] = result.get("customer_code", "")
                        st.session_state["mail_cname"] = result.get("customer_name", "")
                        st.session_state["mail_zip"] = result.get("customer_zip", "")
                        st.session_state["mail_tel"] = result.get("customer_tel", "")
                        st.session_state["mail_addr"] = result.get("customer_address", "")
                        st.session_state["mail_ddate"] = result.get("delivery_date", "")
                        st.session_state["mail_notes"] = result.get("notes", "")
                        st.success("抽出完了！右側で支店や内容を確認・修正してください。")

    with col_m2:
        st.subheader("📝 抽出結果の確認・修正")
        if st.session_state.current_order_mail:
            m_order = st.session_state.current_order_mail
            m_candidates = m_order.get("_candidate_list", [])
            
            # 支店・店舗の選択プルダウン
            if m_candidates:
                m_cand_options = [
                    f"【{c['顧客コード']}】 {c['顧客名']} ｜ 〒{c['郵便番号']} ｜ {c['住所']} ｜ TEL:{c['電話番号']}"
                    for c in m_candidates
                ]
                if len(m_candidates) > 1:
                    st.warning(f"⚠️ 同名・関連する店舗が **{len(m_candidates)} 件** 見つかりました。該当の支店を選択してください：")
                else:
                    st.success(f"🎯 顧客マスタと一致しました（支店の変更も可能です）：")
                
                st.selectbox(
                    "🏢 登録店舗・支店の切り替え", 
                    m_cand_options, 
                    key="mail_cand_select",
                    on_change=on_mail_branch_change
                )
            
            col_mc1, col_mc2 = st.columns([1, 2])
            m_code = col_mc1.text_input("顧客コード", key="mail_ccode")
            m_name = col_mc2.text_input("顧客名・会社名", key="mail_cname")
            
            col_mf1, col_mf2, col_mf3 = st.columns([1, 1, 1])
            m_zip = col_mf1.text_input("郵便番号", key="mail_zip")
            m_tel = col_mf2.text_input("電話番号", key="mail_tel")
            m_ddate = col_mf3.text_input("希望納期", key="mail_ddate")
            
            m_addr = st.text_input("住所・納品先", key="mail_addr")
            
            st.write("▼ 注文明細")
            m_items = m_order.get("items", [])
            mail_items_to_save = []
            for i, itm in enumerate(m_items):
                c_i1, c_i2, c_i3 = st.columns([3, 1, 1])
                name = c_i1.text_input(f"品名/品番 #{i+1}", value=itm.get("item_name", ""), key=f"mail_item_{i}")
                qty = c_i2.number_input(f"数量 #{i+1}", value=int(itm.get("quantity", 1)), min_value=1, key=f"mail_qty_{i}")
                unit = c_i3.text_input(f"単位 #{i+1}", value=itm.get("unit", "個"), key=f"mail_unit_{i}")
                mail_items_to_save.append({"name": name, "qty": qty, "unit": unit, "price": 0})
            
            m_notes = st.text_area("備考", key="mail_notes")
            if st.button("✅ この内容でメール注文を確定・保存", key="btn_save_mail", type="primary"):
                save_order_items("メール", m_code, m_name, m_zip, m_tel, m_addr, m_ddate, mail_items_to_save, m_notes)
                st.success("メール注文を保存しました！「登録済み注文一覧」タブを確認してください。")
                st.session_state.current_order_mail = None
                st.rerun()

# ==========================================
# 3. 電話（全角・半角 あいまい検索 ＆ 手入力両対応）
# ==========================================
with tab_phone:
    st.subheader("📞 電話受付 - 顧客・商品検索と明細登録")
    col_stat1, col_stat2 = st.columns(2)
    col_stat1.caption(f"※ 顧客マスタ: **{len(df_cust_master):,}** 件")
    col_stat2.caption(f"※ 商品マスタ: **{len(df_items_master):,}** 件")
    
    st.markdown("#### 1. 顧客の検索・選択")
    col_cs1, col_cs2 = st.columns([2, 1])
    cust_query_raw = col_cs1.text_input("🔍 顧客検索（コード・社名・郵便・TEL・住所 ※全角半角どちらでも可）", placeholder="例: 創美、アタゴ、910、0776、館林市 など", key="cust_search_query")
    req_date = col_cs2.date_input("希望納期", key="phone_date")

    norm_cust_q = normalize_text(cust_query_raw)
    if norm_cust_q:
        matched_cust = df_cust_master[
            df_cust_master["_search_text"].str.contains(norm_cust_q, na=False)
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
    
    item_mode = st.radio(
        "追加方法の選択:",
        ["🔍 マスタから検索して追加", "✏️ マスタにない商品・特注品を手入力で追加"],
        horizontal=True
    )
    
    if item_mode == "🔍 マスタから検索して追加":
        col_is1, col_is2 = st.columns([2, 3])
        item_query_raw = col_is1.text_input("🔍 商品検索（コード・商品名・略称 ※全角半角どちらでも可）", placeholder="例: AK、BL、500102、ノズル など", key="item_search_query")
        
        norm_item_q = normalize_text(item_query_raw)
        if norm_item_q:
            matched_items = df_items_master[
                df_items_master["_search_text"].str.contains(norm_item_q, na=False)
            ]
        else:
            matched_items = df_items_master.head(50)

        item_options = [
            f"【{row['品番']}】 {row['品名']} ｜ 単位: {row['単位']} ｜ 単価: ¥{int(row['標準単価']):,}"
            for _, row in matched_items.iterrows()
        ]

        if item_options:
            selected_item_str = col_is2.selectbox(
                f"商品候補（該当 {len(matched_items)} 件）", 
                item_options, 
                key=f"item_select_box_{norm_item_q}"
            )
            
            selected_code = selected_item_str.split("】")[0].replace("【", "").strip()
            item_row = df_items_master[df_items_master["品番"].astype(str) == selected_code].iloc[0]
            selected_name = str(item_row["品名"])
            auto_unit = str(item_row["単位"])
            auto_price = int(item_row["標準単価"])

            col_p1, col_p2, col_p3, col_p4 = st.columns([1, 1, 1, 1])
            qty = col_p1.number_input("数量", min_value=1, value=1, key=f"qty_{selected_code}_{norm_item_q}")
            item_unit_input = col_p2.text_input("単位", value=auto_unit, key=f"unit_{selected_code}_{norm_item_q}")
            unit_price = col_p3.number_input("単価（円）", value=auto_price, step=100, key=f"price_{selected_code}_{norm_item_q}")
            
            with col_p4:
                st.write("")
                st.write("")
                if st.button("＋ 明細に追加", use_container_width=True, key="btn_add_master_item"):
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
            st.warning("⚠️ 一致する商品が見つかりません。上の「✏️ マスタにない商品・特注品を手入力で追加」を選ぶと直接手入力で登録できます。")

    else:
        st.info("💡 マスタに登録されていない商品・修理費用・特注品などを直接手入力してカートに追加できます。")
        col_m1, col_m2 = st.columns([1, 2])
        manual_code = col_m1.text_input("品番・商品コード（任意）", value="-", key="manual_item_code")
        manual_name = col_m2.text_input("商品名・品名（必須）", placeholder="例: 特注超音波アタッチメント加工代", key="manual_item_name")
        
        col_m3, col_m4, col_m5, col_m6 = st.columns([1, 1, 1, 1])
        manual_qty = col_m3.number_input("数量", min_value=1, value=1, key="manual_item_qty")
        manual_unit = col_m4.text_input("単位", value="個", key="manual_item_unit")
        manual_price = col_m5.number_input("単価（円）", min_value=0, value=0, step=100, key="manual_item_price")
        
        with col_m6:
            st.write("")
            st.write("")
            if st.button("＋ 手入力商品を追加", use_container_width=True, key="btn_add_manual_item"):
                if not manual_name.strip():
                    st.error("商品名を入力してください。")
                else:
                    st.session_state.phone_cart.append({
                        "code": manual_code.strip() if manual_code.strip() else "-",
                        "name": manual_name.strip(),
                        "qty": manual_qty,
                        "unit": manual_unit.strip(),
                        "price": manual_price,
                        "subtotal": manual_qty * manual_price
                    })
                    st.success(f"「{manual_name}」をカートに追加しました！")
                    st.rerun()

    # カート明細一覧
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
        st.info("商品を選択または手入力して「＋ 明細に追加」を押してください。")

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
