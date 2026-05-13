"""
generate_mock_pdf.py

Standalone script that creates mock_script.pdf — a fictional short screenplay
designed to trigger as many production departments as possible for end-to-end testing.

Usage:
    python generate_mock_pdf.py

Generates: mock_script.pdf in the current directory

Departments deliberately triggered:
  - Props          : rotary phone, prop gun (also Stunts & Safety)
  - Wardrobe       : 1940s costumes (period setting)
  - Art Dept       : 1940s set dressing, artificial snow
  - Camera         : drone aerial shot
  - Grip & Electric: fog machine, rain tower, night lighting, LED wall
  - Locations      : EXT. City Plaza permit
  - Stunts & Safety: car chase, explosion, gun (armourer), stunt fall
  - VFX            : LED wall VFX supervision
  - Cast & Extras  : crowd scene
  - Transportation : picture vehicles (car chase)
  - Catering       : (mentioned in passing)
  - Sound          : (ambient scene — sound recordist implied)
  - Miscellaneous  : dog + handler
"""

from fpdf import FPDF


class ScreenplayPDF(FPDF):
    """Minimal screenplay formatter."""

    MARGIN_LEFT = 30
    MARGIN_RIGHT = 20
    FONT = "Courier"

    def __init__(self):
        super().__init__(format="Letter")
        self.set_margins(self.MARGIN_LEFT, 20, self.MARGIN_RIGHT)
        self.set_auto_page_break(auto=True, margin=20)
        self.add_page()
        self.set_font(self.FONT, size=12)

    def scene_heading(self, text: str):
        self.ln(6)
        self.set_font(self.FONT, style="B", size=12)
        self.multi_cell(0, 6, text.upper())
        self.set_font(self.FONT, size=12)

    def action(self, text: str):
        self.ln(4)
        self.multi_cell(0, 6, text)

    def character(self, name: str):
        self.ln(4)
        self.set_x(self.MARGIN_LEFT + 40)
        self.cell(0, 6, name.upper())
        self.ln()

    def dialogue(self, text: str):
        self.set_left_margin(self.MARGIN_LEFT + 20)
        self.set_right_margin(self.MARGIN_RIGHT + 20)
        self.multi_cell(0, 6, text)
        self.set_left_margin(self.MARGIN_LEFT)
        self.set_right_margin(self.MARGIN_RIGHT)

    def transition(self, text: str):
        self.ln(4)
        self.set_x(self.MARGIN_LEFT + 80)
        self.cell(0, 6, text.upper())
        self.ln()

    def title_page(self):
        self.ln(60)
        self.set_font(self.FONT, style="B", size=16)
        self.cell(0, 10, "THE LAST FOG", align="C")
        self.ln(10)
        self.set_font(self.FONT, size=12)
        self.cell(0, 8, "Written by", align="C")
        self.ln(8)
        self.cell(0, 8, "A. Producer", align="C")
        self.ln(8)
        self.cell(0, 8, "(Test script for breakdown tool)", align="C")
        self.add_page()


def build_script() -> ScreenplayPDF:
    pdf = ScreenplayPDF()
    pdf.title_page()

    # -- PAGE 2: Opening - 1940s city, fog, night
    pdf.scene_heading("FADE IN:")
    pdf.scene_heading("EXT. CITY PLAZA - NIGHT")
    pdf.action(
        "A thick mist hangs low over the cobblestones of CITY PLAZA, a grand public "
        "square ringed by art-deco buildings. It is 1947. The streets glisten with "
        "the remnants of a downpour. Gas-lamp halos flicker through the haze.\n\n"
        "A dozen BYSTANDERS in period overcoats hurry past, collars raised against "
        "the damp. Hundreds more fill the plaza behind them, barely visible through "
        "the fog."
    )
    pdf.action(
        "DETECTIVE MARLOWE (50s, rumpled fedora, unlit cigarette) stands at the "
        "center of it all, staring up at the neon sign of the ATLAS HOTEL."
    )
    pdf.character("MARLOWE")
    pdf.dialogue("(to himself)\nSomebody always leaves the light on.")
    pdf.action(
        "A GERMAN SHEPHERD trots to Marlowe's side, sits, and looks up at him."
    )
    pdf.character("MARLOWE")
    pdf.dialogue("Not you, Rex. You never leave the light on.")

    # -- PAGE 3: Interior - 1940s bar
    pdf.add_page()
    pdf.scene_heading("INT. ATLAS HOTEL BAR - CONTINUOUS")
    pdf.action(
        "The bar is a shrine to the 1940s -- mahogany paneling, velvet stools, "
        "a rotary phone on the counter. Behind the bar, a massive LED WALL cycles "
        "through a projected city skyline, anachronistically modern."
    )
    pdf.action(
        "VERA (30s, red dress, sharp eyes) sits at the end of the bar. She watches "
        "Marlowe enter."
    )
    pdf.character("VERA")
    pdf.dialogue("You're late.")
    pdf.character("MARLOWE")
    pdf.dialogue("Traffic.")
    pdf.action(
        "Marlowe picks up the rotary phone on the counter. Dials. No answer. "
        "Sets it down."
    )
    pdf.action(
        "A WAITER sets down two glasses. Behind them, catering trays are stacked "
        "near the service door."
    )

    # -- PAGE 4: Rooftop - stunt fall, drone
    pdf.add_page()
    pdf.scene_heading("EXT. ATLAS HOTEL ROOFTOP - NIGHT")
    pdf.action(
        "A DRONE SHOT sweeps over the rooftop, revealing the full scale of the "
        "city below -- thousands of lights stretching to the horizon."
    )
    pdf.action(
        "THE ASSASSIN (masked, athletic) sprints across the rooftop and LAUNCHES "
        "himself off the edge -- a three-story free fall, arms spread."
    )
    pdf.action(
        "He SLAMS onto the canvas awning of the hotel entrance below, rolls, and "
        "disappears into the crowd. Passersby scatter."
    )
    pdf.character("MARLOWE")
    pdf.dialogue("(into radio)\nHe's in the plaza. All units move.")

    # -- PAGE 5: Car chase
    pdf.add_page()
    pdf.scene_heading("EXT. CITY STREETS - NIGHT - CONTINUOUS")
    pdf.action(
        "A BLACK SEDAN tears around the corner, fishtailing on the wet cobblestones. "
        "Marlowe's POLICE CRUISER roars in pursuit, siren wailing."
    )
    pdf.action(
        "The two vehicles race through narrow 1940s-dressed streets -- a full car "
        "chase through practical locations. The sedan clips a fire hydrant, sending "
        "a GEYSER of water into the air."
    )
    pdf.action(
        "The assassin leans from the sedan window and FIRES a pistol. Marlowe "
        "ducks as the windshield SHATTERS."
    )
    pdf.action(
        "The sedan takes a hard turn and EXPLODES through a fruit stand before "
        "screeching to a halt at the end of a dead-end alley."
    )

    # -- PAGE 6: Climax - explosion, snow
    pdf.add_page()
    pdf.scene_heading("EXT. DEAD-END ALLEY - CONTINUOUS")
    pdf.action(
        "Marlowe skids to a stop. Gets out. The assassin stands at the far end, "
        "back to the wall."
    )
    pdf.action(
        "Suddenly -- an EXPLOSION tears through the wall behind the assassin. "
        "Brick and debris rain down. A safety officer's radio crackles in the b.g."
    )
    pdf.action(
        "Snow begins to fall -- thick, artificial-looking flakes that dust the "
        "alley in white. Frost creeps along the gutters."
    )
    pdf.action(
        "Marlowe raises his revolver. The assassin does the same."
    )
    pdf.character("MARLOWE")
    pdf.dialogue("It ends here.")
    pdf.action("A long beat. The snow falls.")
    pdf.transition("SMASH CUT TO BLACK.")
    pdf.scene_heading("FADE OUT.")

    return pdf


def main():
    output_path = "mock_script.pdf"
    print(f"Generating mock script PDF: {output_path}")
    pdf = build_script()
    pdf.output(output_path)
    print(f"Done. Written to: {output_path}")
    print("\nDepartments this script should trigger:")
    print("  Props           — rotary phone, pistol/revolver (prop weapon)")
    print("  Wardrobe        — 1940s/period costumes, period set")
    print("  Art Dept        — 1940s set dressing, artificial snow")
    print("  Camera          — drone aerial shot")
    print("  Grip & Electric — fog machine, rain tower, night lighting, LED wall")
    print("  Locations       — EXT. City Plaza permit")
    print("  Stunts & Safety — car chase, explosion, 3-story fall, prop gun armourer")
    print("  VFX             — LED wall in-camera VFX supervision")
    print("  Cast & Extras   — crowd scene (hundreds of bystanders)")
    print("  Transportation  — black sedan + police cruiser (picture vehicles)")
    print("  Catering        — catering trays (background mention)")
    print("  Sound           — (ambient recording implied)")
    print("  Miscellaneous   — German Shepherd + licensed handler")


if __name__ == "__main__":
    main()
