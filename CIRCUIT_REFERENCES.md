# Circuit design reference

Use Stephen Brown and Zvonko Vranesic, *Fundamentals of Digital Logic with VHDL Design*, third edition, McGraw-Hill, 2009, ISBN 978-0-07-352953-0, as the preferred reference for circuit logic and behavior.

The entries below were checked against the text and diagrams in `Fundamentals-of-Digital-Logic-with-VHDL-Design-3rd-edition.pdf`. PDF pages are **one-based viewer page numbers** for that 961-page copy; printed page numbers identify the book pages. Other PDF copies may have different offsets.

| Circuit | Section | Figure | Printed page | PDF page |
| --- | --- | --- | --- | --- |
| Gated D latch | 7.3 | 7.8 | 389 | 410 |
| Master-slave D flip-flop | 7.4.1 | 7.10 | 392 (discussion starts on 391) | 413 |
| T flip-flop | 7.5 | 7.16 | 399 (discussion starts on 398) | 420 |
| JK flip-flop | 7.6 | 7.17 | 400 | 421 |

## Design notes

These notes summarize behavior in our own words; they do not replace the book diagrams.

- **D latch:** Figure 7.8 uses an inverter, two NAND input gates, and a cross-coupled NAND latch. `Clk=1` allows `Q` to follow `D`; `Clk=0` retains the stored value. It is level-sensitive.
- **Master-slave D:** Figure 7.10 connects two gated D latches with opposite enable phases. The master accepts data while the clock is high; the slave accepts the master's output while the clock is low. The external output therefore updates on the **falling edge**.
- **T flip-flop:** Figure 7.16 feeds a **rising-edge D flip-flop** with `D = NOT(T) AND Q OR T AND NOT(Q)`. `T=0` holds and `T=1` toggles at the rising edge. `D = T XOR Q` is an equivalent simplification, not the gate arrangement drawn in the figure.
- **JK flip-flop:** Figure 7.17 uses `D = J AND NOT(Q) OR NOT(K) AND Q` with a rising-edge D flip-flop. `JK=00` holds, `01` resets, `10` sets, and `11` toggles.

## Rules for agents

1. Call `digital_design_schema` and read `circuit_reference` before designing a matching circuit. Those entries remain available even when the PDF is not installed on the MCP machine.
2. In the result explanation, cite the authors, third edition, section, figure, and printed page. For example: “Based on Brown and Vranesic, 3rd ed., section 7.3, figure 7.8, p. 389; layout adapted for Digital.”
3. State the enable polarity or active clock edge. Distinguish the book's design from any equivalent gate substitution, clock inversion, reset addition, or other adaptation. Expanding the latches in figure 7.10 into gates also uses figure 7.8.
4. For topics outside the checked entries, inspect the relevant book pages before adding a citation. Do not invent figure numbers or claim to have read a PDF that is unavailable. Identify a design based only on the checked summaries as such.
5. Treat the book as the logic reference. Apply the Digital layout rules separately: 20-unit grid, correct pin endpoints, orthogonal wires, horizontal entry into gate inputs, and separated branches. Preserve behavior while rearranging wires.
6. Check behavior against the characteristic table: latch transparency/hold, D sampling at the selected edge, T hold/toggle, and all four JK cases. Include initialization and non-active-edge checks for sequential circuits; do not treat unknown initial feedback state as a valid test result.

The book is not bundled with this repository. Keep your PDF locally for checking diagrams or looking up additional circuits. This reference guide and the MCP metadata contain the bibliography, verified locations, and original summaries only.
