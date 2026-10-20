# Sistemas Distribuídos Trabalho 1

## Guia de Execução e Testes 

### Pré-requisitos
Antes de iniciar, garanta que a sua máquina possui os seguintes softwares instalados:
1. **Docker Engine / Docker Desktop**
2. **Docker Compose**
3. **Python 3.x** (para rodar o script cliente de teste no host)

---

### Passo 1: Subir os Contêineres do Mini-DNS

No terminal, navegue até a pasta do repositório e execute o comando abaixo para construir as imagens e subir a rede de servidores:

```bash
docker-compose up --build 
```

Este comando irá subir 4 contêineres na rede 172.20.0.0/16:
* root_dns (172.20.0.100:5300)
* tld_dns (172.20.0.101:5300)
* auth_dns (172.20.0.102:5300)
* local_resolver (172.20.0.50:5353, mapeado externamente para a porta UDP 5354)

### Passo 2: Executar o Teste
Abra um novo terminal e execute:

```bash
python client_test.py
```
