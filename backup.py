import os
from datetime import datetime
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from oauth2client.service_account import ServiceAccountCredentials

# Escopo necessário para fazer upload pro Drive
SCOPES = ['https://www.googleapis.com/auth/drive.file']
CREDENTIALS_FILE = 'credenciais.json'
DB_FILE = os.getenv('DB_PATH', 'confeitaria.db')
FOLDER_NAME = 'Backups Assistente'

def autenticar():
    import json
    creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
    if creds_json:
        creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(creds_json), SCOPES)
        return build('drive', 'v3', credentials=creds)
    elif os.path.exists(CREDENTIALS_FILE):
        creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, SCOPES)
        return build('drive', 'v3', credentials=creds)
    else:
        print(f"Erro: Nenhuma credencial do Google encontrada.")
        return None

def obter_ou_criar_pasta(drive_service):
    # Procura se a pasta já existe
    query = f"name='{FOLDER_NAME}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    results = drive_service.files().list(q=query, spaces='drive', fields='files(id, name)').execute()
    pastas = results.get('files', [])
    
    if pastas:
        return pastas[0]['id']
        
    # Se não existir, cria a pasta
    print(f"Criando pasta '{FOLDER_NAME}' no Google Drive...")
    file_metadata = {
        'name': FOLDER_NAME,
        'mimeType': 'application/vnd.google-apps.folder'
    }
    pasta = drive_service.files().create(body=file_metadata, fields='id').execute()
    return pasta.get('id')

def realizar_backup():
    if not os.path.exists(DB_FILE):
        print("Erro: Banco de dados confeitaria.db não encontrado. Nada para fazer backup.")
        return False
        
    drive_service = autenticar()
    if not drive_service:
        return False
        
    pasta_id = obter_ou_criar_pasta(drive_service)
    
    data_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    nome_arquivo_backup = f"confeitaria_backup_{data_str}.db"
    
    file_metadata = {
        'name': nome_arquivo_backup,
        'parents': [pasta_id]
    }
    
    media = MediaFileUpload(DB_FILE, mimetype='application/octet-stream', resumable=True)
    
    print(f"Fazendo upload do backup: {nome_arquivo_backup} ...")
    file = drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()
    print(f"✅ Backup concluído! ID do arquivo: {file.get('id')}")
    
    # Opcional: Apagar backups muito antigos para não lotar o Drive
    limpar_backups_antigos(drive_service, pasta_id)
    return True

def limpar_backups_antigos(drive_service, pasta_id, max_backups=7):
    """Mantém apenas os últimos 'max_backups' arquivos na pasta"""
    query = f"'{pasta_id}' in parents and trashed=false"
    results = drive_service.files().list(
        q=query, 
        orderBy="createdTime desc", 
        fields="files(id, name)"
    ).execute()
    
    arquivos = results.get('files', [])
    
    if len(arquivos) > max_backups:
        arquivos_para_apagar = arquivos[max_backups:]
        print(f"Limpando {len(arquivos_para_apagar)} backups antigos...")
        for arq in arquivos_para_apagar:
            try:
                drive_service.files().delete(fileId=arq['id']).execute()
            except Exception as e:
                print(f"Erro ao apagar backup {arq['name']}: {e}")

if __name__ == "__main__":
    realizar_backup()
