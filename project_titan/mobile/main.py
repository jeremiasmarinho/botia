from __future__ import annotations

from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label


class TitanMobileRoot(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(orientation="vertical", spacing=12, padding=16, **kwargs)

        self.status = Label(text="Project Titan PoC (Offline)", font_size="20sp")
        self.add_widget(self.status)

        self.info = Label(
            text="Modo seguro de demonstração: sem automação de terceiros.\nUse a API local /equity para testes.",
            font_size="14sp",
        )
        self.add_widget(self.info)

        run_button = Button(text="Simular decisão", size_hint=(1, 0.2))
        run_button.bind(on_press=self.simulate_decision)
        self.add_widget(run_button)

    def simulate_decision(self, _instance):
        self.status.text = "Simulação OK: Click simulated at 320,240"


class TitanMobileApp(App):
    def build(self):
        self.title = "Project Titan Mobile PoC"
        return TitanMobileRoot()


if __name__ == "__main__":
    TitanMobileApp().run()
