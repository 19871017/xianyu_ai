#!/bin/bash
# 服务器修复脚本 - 一次性修复license_service.py + 重启 + 测试激活
set -e
cd /opt/xf_server
source venv/bin/activate

echo "=== 1. 修复 license_service.py ==="
python3 << 'PYFIX'
with open("services/license_service.py", "r") as f:
    content = f.read()

# 修复1: datetime.now(timezone.utc) -> datetime.utcnow()
content = content.replace("datetime.now(timezone.utc)", "datetime.utcnow()")

# 修复2: activate_license中，改为重新签名而非验证旧签名
# 找到activate_license函数中 "# RSA 签名验证" 到 "return True, "激活成功"" 的部分
import re

# 匹配activate_license中的签名验证块（多种可能格式）
patterns = [
    # 已修复过的版本
    (r'(async def activate_license.*?)    # 激活时重新签名.*?return True, "激活成功"',
     r'''\1    # 激活时重新签名（绑定machine_id），跳过旧签名验证
    from utils.rsa_utils import load_private_key
    private_key = load_private_key()
    lic.machine_id = machine_id
    payload = json.dumps(
        {
            "license_key": lic.license_key,
            "machine_id": lic.machine_id,
            "issued_at": lic.issued_at.isoformat() if lic.issued_at else datetime.utcnow().isoformat(),
            "expires_at": lic.expires_at.isoformat() if lic.expires_at else datetime.utcnow().isoformat(),
        }
    )
    lic.signature = sign_data(private_key, payload.encode("utf-8")).hex()
    await db.commit()
    return True, "激活成功"'''),
    # 原始版本
    (r'(async def activate_license.*?)    # RSA 签名验证.*?return True, "激活成功"',
     r'''\1    # 激活时重新签名（绑定machine_id），跳过旧签名验证
    from utils.rsa_utils import load_private_key
    private_key = load_private_key()
    lic.machine_id = machine_id
    payload = json.dumps(
        {
            "license_key": lic.license_key,
            "machine_id": lic.machine_id,
            "issued_at": lic.issued_at.isoformat() if lic.issued_at else datetime.utcnow().isoformat(),
            "expires_at": lic.expires_at.isoformat() if lic.expires_at else datetime.utcnow().isoformat(),
        }
    )
    lic.signature = sign_data(private_key, payload.encode("utf-8")).hex()
    await db.commit()
    return True, "激活成功"'''),
]

for pattern, replacement in patterns:
    new_content = re.sub(pattern, replacement, content, flags=re.DOTALL)
    if new_content != content:
        content = new_content
        print(f"Matched pattern, patched activate_license")
        break
else:
    print("WARN: No matching pattern found in activate_license")

with open("services/license_service.py", "w") as f:
    f.write(content)
print("license_service.py saved")
PYFIX

echo "=== 2. 查看数据库 ==="
python3 -c "
from sqlalchemy import create_engine, text
from config import DATABASE_URL
engine = create_engine(DATABASE_URL)
with engine.connect() as conn:
    result = conn.execute(text('SELECT name FROM sqlite_master WHERE type=\"table\"'))
    tables = [r[0] for r in result]
    print('Tables:', tables)
    for t in tables:
        if 'licen' in t.lower():
            result2 = conn.execute(text(f'SELECT id, license_key, machine_id, is_active, expires_at FROM {t}'))
            print(f'--- {t} ---')
            for row in result2:
                print(row)
"

echo "=== 3. 重启服务 ==="
systemctl restart xf-server
sleep 3
systemctl status xf-server --no-pager | head -3

echo "=== 4. 测试激活 ==="
sleep 1
curl -s -X POST http://localhost:8000/api/license/activate \
  -H "Content-Type: application/json" \
  -d '{"license_key":"908f49682c2f456d96035a5d78c27cf8","machine_id":"196dc4cf1d32c23b542623327fd04ff5"}'

echo ""
echo "=== 5. 测试验证 ==="
curl -s "http://localhost:8000/api/license/verify?license_key=908f49682c2f456d96035a5d78c27cf8&machine_id=196dc4cf1d32c23b542623327fd04ff5"

echo ""
echo "=== 6. 测试外部访问 ==="
curl -s http://38.71.117.111:8000/ | head -5 || echo "External access failed"

echo ""
echo "=== DONE ==="
