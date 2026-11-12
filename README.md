# Raid Boss Pulse

Crie uma aplicação desktop completa em Python (utilizando bibliotecas como OpenCV, PyAutoGUI, Pillow e Tkinter/PyQt para a interface, ou uma estrutura web local se aplicável, focada em automação de desktop) para monitorar a janela do jogo Lineage 2 com base na imagem fornecida ("World Info" com a aba "Raid" selecionada).

A aplicação deve conter as seguintes funcionalidades principais:

1. **Automação de Clique no Botão "Raid":**

   - O programa deve ser capaz de localizar e clicar automaticamente no botão "Raid" (visível na parte superior da janela do jogo) em intervalos configuráveis de tempo (ex: a cada X segundos ou minutos) para atualizar a lista de chefes de raid.

2. **Monitoramento Visual da Lista de Bosses:**

   - A aplicação deve capturar a região da lista de bosses mostrada na imagem (que contém itens como `[Lv.87] Core of Chaos (Standby)`, `[Lv.90] Antharas (Standby)`, etc.).

   - O sistema deve monitorar pixels específicos ou usar reconhecimento de imagem/OCR para detectar quando o status de "Standby" ou a cor do texto/fundo de algum dos bosses mudar (indicando que o boss nasceu ou mudou de estado).

3. **Alerta Sonoro e Visual no Windows:**

   - Assim que houver mudança na cor ou no status de qualquer boss da lista monitorada, o programa deve emitir um alerta sonoro nativo do Windows e exibir uma notificação visual na tela (como uma janela de destaque ou notificação toast) informando qual boss teve o status alterado.

4. **Interface de Controle Simples:**

   - Uma interface gráfica leve com botões de "Iniciar Monitoramento", "Parar", ajuste do intervalo de clique no botão "Raid" e campos para calibrar/selecionar a região da tela do jogo a ser monitorada.

This project was built with [Lovable](https://lovable.dev).

**Live app**: https://raid-boss-pulse.lovable.app

## Build with Lovable

Continue developing this project in the [Lovable editor](https://lovable.dev/projects/46a09d52-a1ae-4fd9-8db7-3e62725a27d5).

- **Ship faster**: describe what you want to build and Lovable handles the code.
- **Stay in sync**: every change made in Lovable is committed straight to this repository.
- **Full ownership**: this code is yours. Push to `main` on GitHub and your changes sync back into Lovable, ready for your next prompt.

## Development

Prefer working locally? You need Node.js and npm — [install with nvm](https://github.com/nvm-sh/nvm#installing-and-updating).

```sh
git clone <this-repository-url>
cd <repository-name>
npm i
npm run dev
```
