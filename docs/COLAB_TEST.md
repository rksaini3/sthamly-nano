# Colab par Sthamly Desk test karna

Ye notes Colab par Desk ko end-to-end test karne ke working steps hain.

## 1. Repo aur dependencies
- Clone: git clone -b main https://github.com/rksaini3/sthamly-nano.git /content/sthamly-nano
- Install: pip install -q -r /content/sthamly-nano/requirements-desk.txt

## 2. llama-server aur model
- llama.cpp ka Linux build download karo, phir glob se llama-server dhoondho (/content/llama/**/llama-server).
- Uske folder ki saari files /content/sthamly-nano/ mein copy karo, aur llama-server ko chmod 755 karo.
- Model: Qwen/Qwen2.5-1.5B-Instruct-GGUF, file qwen2.5-1.5b-instruct-q4_k_m.gguf, save as /content/sthamly-nano/models/test.gguf

## 3. Desk chalana (dev mode, bina licence)
- Env: STHAMLY_DEV=1 aur LD_LIBRARY_PATH=/content/sthamly-nano
- desk/sthamly_desk.py ko --model, --llama-server (absolute paths), --no-license, --no-browser, --port 8765, --ctx 2048 ke saath chalao.
- 60 se 90 second baad http://127.0.0.1:8765/ par 200 aana chahiye.

## 4. Gotchas
- llama.cpp build 12318 mein --flash-attn ko value chahiye (on, off, auto). desk/desk_llama.py mein "--flash-attn", "on" hona chahiye. Bina value ke llama-server usage error deta tha aur desk Model shuru nahi hua dikhata tha.
- os.system ka output Colab mein kabhi nahi dikhta, isliye subprocess se output print karo.
- Colab runtime reset hone par /content/sthamly-nano dobara clone aur download karna padta hai.
- Service-role key aur GitHub token kabhi chat mein paste mat karo, getpass use karo.

## 5. Test licence
- Key scripts/licenses.py se banao, aur test ke baad revoke --key se band karo.
