import streamlit as st
import pandas as pd
import json
import os
import io
from datetime import datetime
import google.generativeai as genai
from PIL import Image

# PDF生成用ライブラリ
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont

# --------------------------------------------------
# 初期設定
# --------------------------------------------------
st.set_page_config(page_title="受発注DXタブレットアプリ", layout="wide")

CSV_FILE = "orders_data.csv"

# 日本語フォント登録（PDF用）
pdfmetrics.registerFont(UnicodeCIDFont('HeiseiKakuGo-W5'))

# PDF帳票生成関数
def generate_pdf_report(df):
    buffer = io.BytesIO()
    # A4横向きレイアウト
    doc = SimpleDocTemplate(
        buffer, 
        pagesize=landscape(A4),
        rightMargin=20, leftMargin=20, topMargin=25, bottomMargin=25
    )
    elements = []
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle(
        name='TitleStyle',
        fontName='HeiseiKakuGo-W5',
        fontSize=18,
        leading=22,
        alignment=1, # 中央揃え
        textColor=colors.HexColor('#1E293B')
    )
    meta_style = ParagraphStyle(
        name='MetaStyle',
        fontName='HeiseiKakuGo-W5',
        fontSize=9,
        leading=12,
        alignment=2, # 右揃え
        textColor=colors.HexColor('#64748B')
    )
    cell_style = ParagraphStyle(
        name='CellStyle',
        fontName='HeiseiKakuGo-W5',
        fontSize=8,
        leading=10,
        textColor=colors.HexColor('#0F172A')
    )
    header_style = ParagraphStyle(
        name='HeaderStyle',
        fontName='HeiseiKakuGo-W5',
        fontSize=8.5,
        leading=11,
        fontName_bold='HeiseiKakuGo-W5',
        textColor=colors.whitesmoke,
        alignment=1
    )

    # タイトルと出力日時
    elements.append(Paragraph("受 発 注 伝 票 一 覧 表", title_style))
    elements.append(Spacer(1, 5))
    elements.append(Paragraph(f"出力日時: {datetime.now().strftime('%Y年%m月%d日 %H:%M')}", meta_style))
    elements.append(Spacer(1, 10))

    # 表データ整形
    headers = ["注文日時", "種別", "顧客名", "電話番号", "住所・納品先", "品番", "品名", "数量", "単価", "小計", "納期"]
    table_data = [[Paragraph(h, header_style) for h in headers]]

    for _, row in df.iterrows():
        # 日時を短く成形
        dt_str = str(row.get("注文日時", ""))[:16]
        table_data.append([
            Paragraph(dt_str, cell_style),
            Paragraph(str(row.get("受付種別", "")), cell_style),
            Paragraph(str(row.get("顧客名", "")), cell_style),
            Paragraph(str(row.get("電話番号", "")), cell_style),
            Paragraph(str(row.get("住所", "")), cell_style),
            Paragraph(str(row.get("品番", "")), cell_style),
            Paragraph(str(row.get("品名", "")), cell_style),
            Paragraph(str(row.get("数量", "")), cell_style),
            Paragraph(f"¥{int(row.get('単価', 0)):,}", cell_style),
            Paragraph(f"¥{int(row.get('小計', 0)):,}", cell_style),
            Paragraph(str(row.get("希望納期", "")), cell_style),
        ])

    # A4横（約800pt）に合わせた列幅設定
    col_widths = [75, 30, 85, 65, 140, 50, 110, 30, 45, 50, 60]
    
    t = Table(table_data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1E293B')),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#CBD5E1')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F8FAFC')]),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    elements.append(t)
    doc.build(elements)
    buffer.seek(0)
    return buffer

# 注文保存用の共通関数
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
            "電話番号": tel,
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
    if not os.path.exists(CSV_FILE):
        df_new.to_csv(CSV_FILE, index=False, encoding="utf-8-sig")
    else:
        df_new.to_csv(CSV_FILE, mode="a", header=False, index=False, encoding="utf-8-sig")

# APIキー設定
GEMINI_API_KEY = st.secrets.get("GEMINI_API_KEY", "YOUR_GEMINI_API_KEY")
if GEMINI_API_KEY != "YOUR_GEMINI_API_KEY":
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-1.5-flash")

# 商品マスタ
ITEM_MASTER = {
    "A-101": {"name": "超音波ノズル先端部品", "price": 12000},
    "A-102": {"name": "高圧ホース 3m", "price": 8500},
    "B-201": {"name": "専用洗浄溶剤 5L", "price": 4500},
    "B-202": {"name": "皮革用リカラー染料（ブラック）", "price": 3200},
    "C-301": {"name": "交換用パッキンセット", "price": 1500},
}

# 顧客マスタ
CUSTOMER_MASTER = {
    "株式会社サンプル商事 本社": {"tel": "03-1234-5678", "address": "東京都千代田区丸の内1-1-1"},
    "株式会社サンプル商事 大阪支店": {"tel": "06-9876-5432", "address": "大阪府大阪市北区梅田2-2-2"},
    "東京リユース工業": {"tel": "03-3333-4444", "address": "東京都大田区羽田旭町3-3"},
    "埼玉メンテナンス": {"tel": "048-555-6666", "address": "埼玉県さいたま市大宮区桜木町4-4"},
    "その他（新規入力）": {"tel": "", "address": ""}
}

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

tab_fax, tab_mail, tab_phone, tab_list = st.tabs([
    "📠 FAX（写真・スキャン）", 
    "✉️ メール（コピペ解析）", 
    "📞 電話（複数商品入力）", 
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

# 2. メール
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

# 3. 電話
with tab_phone:
    st.subheader("📞 電話受付 - 顧客情報と複数商品登録")
    col_c1, col_c2 = st.columns(2)
    selected_customer_key = col_c1.selectbox("顧客マスタから選択", list(CUSTOMER_MASTER.keys()), key="phone_customer_select")
    req_date = col_c2.date_input("希望納期", key="phone_date")
    
    default_tel = CUSTOMER_MASTER[selected_customer_key]["tel"]
    default_address = CUSTOMER_MASTER[selected_customer_key]["address"]
    
    col_info1, col_info2, col_info3 = st.columns([2, 2, 3])
    cust_name_final = col_info1.text_input("顧客名", value="" if selected_customer_key == "その他（新規入力）" else selected_customer_key, key="phone_cust_name")
    cust_tel_final = col_info2.text_input("電話番号", value=default_tel, key="phone_tel_input")
    cust_addr_final = col_info3.text_input("住所・納品先", value=default_address, key="phone_addr_input")
    
    st.markdown("---")
    st.write("### 🛒 商品の追加")
    col_p1, col_p2, col_p3, col_p4 = st.columns([3, 1, 1, 1])
    selected_code = col_p1.selectbox("商品選択", options=list(ITEM_MASTER.keys()), format_func=lambda x: f"【{x}】 {ITEM_MASTER[x]['name']}")
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

# 4. 登録済み一覧（PDF印刷ボタン付き）
with tab_list:
    st.subheader("📋 登録済み注文一覧（最新データ）")
    
    if os.path.exists(CSV_FILE):
        try:
            df_orders = pd.read_csv(CSV_FILE, encoding="utf-8-sig")
            if not df_orders.empty:
                st.dataframe(df_orders, use_container_width=True)
                
                col_d1, col_d2, col_d3 = st.columns([2, 2, 1])
                csv_data = df_orders.to_csv(index=False, encoding="utf_8_sig").encode("utf_8_sig")
                
                col_d1.download_button(
                    label="📥 販売管理・配送連携用CSV",
                    data=csv_data,
                    file_name=f"orders_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                    mime="text/csv",
                )
                
                # PDF帳票の生成とダウンロード・印刷ボタン
                pdf_bytes = generate_pdf_report(df_orders)
                col_d2.download_button(
                    label="🖨️ A4印刷用PDF帳票（全項目）を出力",
                    data=pdf_bytes,
                    file_name=f"order_report_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf",
                    mime="application/pdf",
                    type="primary"
                )
                
                if col_d3.button("🗑️ 全件クリア"):
                    os.remove(CSV_FILE)
                    st.rerun()
            else:
                st.info("まだ登録された注文データはありません。")
        except Exception as e:
            st.warning(f"データの読み込み待機中: {e}")
    else:
        st.info("まだ登録された注文データはありません。")
