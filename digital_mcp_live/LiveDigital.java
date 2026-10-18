import de.neemann.digital.draw.elements.Circuit;
import de.neemann.digital.draw.shapes.ShapeFactory;
import de.neemann.digital.gui.Main;
import de.neemann.digital.gui.components.CircuitComponent;
import java.awt.*;
import java.awt.event.*;
import java.io.*;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import javax.swing.*;

/** Native Digital renderer; the session producer owns immutable cumulative snapshots. */
class LiveDigital {
    final Path directory;
    final Main frame;
    final CircuitComponent canvas;
    final ShapeFactory shapes;
    final JLabel status = new JLabel("Waiting for circuit…");
    final JPanel controls = new JPanel(new FlowLayout(FlowLayout.LEFT));
    final JPanel shield = new JPanel(new BorderLayout());
    final Map<Component, Boolean> enabled = new IdentityHashMap<>();
    final WindowListener[] nativeClose;
    final int nativeCloseOperation;
    final ScheduledExecutorService reader = Executors.newSingleThreadScheduledExecutor(r -> {
        Thread t = new Thread(r, "digital-live-reader"); t.setDaemon(true); return t;
    });
    final javax.swing.Timer animation;
    final KeyEventDispatcher keys;
    volatile boolean locked = true;
    long seen = -1, revision = -1;
    int elements, wires, batch = 1;
    String seenState = "";
    Update target, lastApplied;
    boolean navigated;
    record Update(long revision, Path snapshot, Circuit circuit, String state, Path output, String message) {}

    LiveDigital(Path directory) throws Exception {
        this.directory = directory.toRealPath();
        frame = new Main.MainBuilder().keepPrefMainFile().setCircuit(new Circuit()).build();
        canvas = frame.getCircuitComponent();
        shapes = frame.getLibrary().getShapeFactory();
        try { target = readUpdate(true); seen = target.revision(); seenState = target.state(); }
        catch (Exception error) { frame.dispose(); throw error; }
        frame.setTitle("Digital — Live build");
        nativeClose = frame.getWindowListeners();
        nativeCloseOperation = frame.getDefaultCloseOperation();
        for (WindowListener listener : nativeClose) frame.removeWindowListener(listener);
        frame.setDefaultCloseOperation(WindowConstants.DISPOSE_ON_CLOSE);
        frame.addWindowListener(new WindowAdapter() {
            public void windowClosing(WindowEvent e) {
                if (locked) { locked = false; shutdown(); writeState("closed", "Window closed during build"); }
            }
            public void windowClosed(WindowEvent e) {
                locked = false; shutdown(); writeState("closed", "Window closed");
            }
        });
        rememberAndDisable(frame.getContentPane());
        if (frame.getJMenuBar() != null) rememberAndDisable(frame.getJMenuBar());
        JButton fit = new JButton("Fit");
        fit.addActionListener(e -> { canvas.fitCircuit(); navigated = true; });
        JButton stop = new JButton("Stop and keep draft");
        stop.addActionListener(e -> stop());
        controls.add(status); controls.add(fit); controls.add(stop);
        shield.setOpaque(false); shield.add(controls, BorderLayout.NORTH);
        MouseAdapter navigation = new MouseAdapter() {
            Point previous;
            public void mousePressed(MouseEvent e) { previous = e.getPoint(); }
            public void mouseReleased(MouseEvent e) { previous = null; }
            public void mouseDragged(MouseEvent e) {
                if (previous != null && onCanvas(e)) {
                    canvas.translateCircuit(e.getX() - previous.x, e.getY() - previous.y);
                    navigated = true;
                }
                previous = e.getPoint();
            }
            public void mouseWheelMoved(MouseWheelEvent e) {
                if (onCanvas(e)) { canvas.scaleCircuit(Math.pow(1.12, -e.getPreciseWheelRotation())); navigated = true; }
            }
        };
        shield.addMouseListener(navigation); shield.addMouseMotionListener(navigation); shield.addMouseWheelListener(navigation);
        frame.setGlassPane(shield); shield.setVisible(true);
        keys = event -> {
            if (!locked || KeyboardFocusManager.getCurrentKeyboardFocusManager().getFocusedWindow() != frame) return false;
            // Only the companion buttons receive keyboard activation; native shortcuts cannot edit or simulate.
            return !(SwingUtilities.isDescendingFrom(event.getComponent(), controls)
                && !event.isControlDown() && !event.isAltDown() && !event.isMetaDown()
                && (event.getKeyCode() == KeyEvent.VK_TAB || event.getKeyCode() == KeyEvent.VK_SPACE
                    || event.getKeyCode() == KeyEvent.VK_ENTER || event.getKeyCode() == KeyEvent.VK_SHIFT));
        };
        KeyboardFocusManager.getCurrentKeyboardFocusManager().addKeyEventDispatcher(keys);
        animation = new javax.swing.Timer(80, e -> animate());
        frame.setSize(1100, 760); frame.setLocationByPlatform(true); frame.setVisible(true);
        stop.requestFocusInWindow();
        if (target.circuit().getElements().isEmpty() && target.circuit().getWires().isEmpty()) {
            revision = target.revision(); lastApplied = target;
        }
        writeState("ready", "Live viewer ready");
        animation.start(); reader.scheduleWithFixedDelay(this::poll, 0, 100, TimeUnit.MILLISECONDS);
    }

    boolean onCanvas(MouseEvent e) {
        Point point = SwingUtilities.convertPoint(shield, e.getPoint(), canvas);
        return canvas.contains(point);
    }

    void rememberAndDisable(Component component) {
        enabled.put(component, component.isEnabled()); component.setEnabled(false);
        if (component instanceof Container container)
            for (Component child : container.getComponents()) rememberAndDisable(child);
    }

    Path snapshot(String name) throws IOException {
        if (name == null || !name.matches("revision-[0-9]+\\.dig")) throw new IOException("Invalid snapshot basename");
        Path file = directory.resolve(name);
        if (!file.toRealPath().getParent().equals(directory) || Files.isSymbolicLink(file))
            throw new IOException("Snapshot escapes session directory");
        return file;
    }

    Update readUpdate(boolean force) throws Exception {
        Properties properties = new Properties();
        try (Reader in = Files.newBufferedReader(directory.resolve("state.properties"), StandardCharsets.UTF_8)) { properties.load(in); }
        long next = Long.parseLong(properties.getProperty("revision", "-1"));
        if (next < 0) throw new IOException("Invalid revision");
        String state = properties.getProperty("state", "");
        if (!state.equals("building") && !state.equals("complete")) throw new IOException("Invalid build state");
        Path file = snapshot(properties.getProperty("snapshot"));
        Path output = null;
        if (state.equals("complete")) {
            URI uri = URI.create(properties.getProperty("output_uri", ""));
            if (!"file".equals(uri.getScheme())) throw new IOException("Final output must be a file URI");
            output = Path.of(uri);
            if (!output.isAbsolute() || !Files.isRegularFile(output)) throw new IOException("Final output is missing");
        }
        if (!force && (next < seen || (next == seen && state.equals(seenState)))) return null;
        Circuit circuit;
        try (InputStream in = Files.newInputStream(file)) { circuit = Circuit.loadCircuit(in, shapes); }
        return new Update(next, file, circuit, state, output, properties.getProperty("message", "Building circuit"));
    }

    void poll() {
        if (!locked) return;
        try {
            Update update = readUpdate(false);
            if (update != null) {
                seen = update.revision(); seenState = update.state();
                SwingUtilities.invokeLater(() -> {
                    if (!locked) return;
                    if (update.circuit().getElements().size() < elements || update.circuit().getWires().size() < wires) {
                        fail(new IOException("Snapshots must be cumulative")); return;
                    }
                    target = update; status.setText(update.message());
                    int remaining = update.circuit().getElements().size() - elements + update.circuit().getWires().size() - wires;
                    // ponytail: coalesce a burst into at most 25 animation frames (~2 seconds).
                    batch = Math.max(1, (remaining + 24) / 25);
                });
            }
        } catch (Exception error) { SwingUtilities.invokeLater(() -> { if (locked) fail(error); }); }
    }

    void animate() {
        if (!locked || target == null) return;
        try {
            Circuit source = target.circuit();
            int before = elements + wires;

            for (int i = 0; i < batch; i++) {
                if (elements < source.getElements().size()) canvas.getCircuit().add(source.getElements().get(elements++));
                else if (wires < source.getWires().size()) canvas.getCircuit().add(source.getWires().get(wires++));
                else break;
            }
            canvas.graphicHasChanged();
            if (!navigated && elements + wires > before) canvas.fitCircuit();
            String message = target.message();
            if (elements == source.getElements().size() && wires == source.getWires().size()) {
                revision = target.revision(); lastApplied = target;
                if (target.state().equals("complete")) { finish(target.output(), "complete", target.message()); return; }
                message = "Waiting for AI… (" + elements + " elements, " + wires + " wires)";
            }
            status.setText(message);
            writeState("building", message);
        } catch (Exception error) { fail(error); }
    }

    void finish(Path file, String state, String message) throws Exception {
        // Preflight before Main.open, whose native implementation catches file errors in a modal dialog.
        try (InputStream in = Files.newInputStream(file)) { Circuit.loadCircuit(in, shapes); }
        enabled.forEach(Component::setEnabled);
        try {
            frame.open(file.toFile(), false);
            if (frame.getBaseFileName() == null || !frame.getBaseFileName().toPath().toAbsolutePath().normalize().equals(file.toAbsolutePath().normalize()))
                throw new IOException("Digital did not open final output");
        } catch (Exception error) {
            enabled.keySet().forEach(component -> component.setEnabled(false));
            throw error;
        }
        locked = false; shutdown();
        frame.setDefaultCloseOperation(nativeCloseOperation);
        shield.setVisible(false);
        for (WindowListener listener : nativeClose) frame.addWindowListener(listener);
        frame.setTitle("Digital — " + file.getFileName());
        frame.setStatus(message); canvas.requestFocusInWindow();
        writeState(state, message);
    }

    void stop() {
        if (!locked) return;
        try {
            // The marker wins the producer race before we read the newest immutable snapshot.
            Files.writeString(directory.resolve("stop"), "Stop requested\n", StandardCharsets.UTF_8, StandardOpenOption.CREATE_NEW);
        } catch (FileAlreadyExistsException ignored) {
        } catch (Exception error) { fail(error); return; }
        try {
            Update latest;
            boolean recovered = false;
            try { latest = Objects.requireNonNull(readUpdate(true), "No snapshot available"); }
            catch (Exception invalidLatest) {
                if (lastApplied == null) throw invalidLatest;
                latest = lastApplied; recovered = true;
                invalidLatest.printStackTrace(System.err);
            }
            Path draft = directory.resolve("stopped.dig");
            for (int suffix = 1; ; suffix++) {
                try { Files.copy(latest.snapshot(), draft); break; }
                catch (FileAlreadyExistsException exists) { draft = directory.resolve("stopped-" + suffix + ".dig"); }
            }
            elements = latest.circuit().getElements().size(); wires = latest.circuit().getWires().size(); revision = latest.revision();
            finish(draft, "stopped", "Build stopped" + (recovered ? "; recovered previous valid snapshot" : "") + "; draft saved to " + draft);
        } catch (Exception error) { fail(error); }
    }

    void fail(Exception error) {
        error.printStackTrace(System.err);
        animation.stop(); reader.shutdownNow();
        status.setText("Error: " + error.getMessage() + " — Stop and keep draft remains available");
        writeState("error", error.toString());
    }

    void shutdown() {
        if (animation != null) animation.stop(); reader.shutdownNow();
        if (keys != null) KeyboardFocusManager.getCurrentKeyboardFocusManager().removeKeyEventDispatcher(keys);
    }

    void writeState(String state, String message) {
        try {
            Properties properties = new Properties();
            properties.setProperty("state", state); properties.setProperty("revision", Long.toString(revision));
            properties.setProperty("elements", Integer.toString(elements)); properties.setProperty("wires", Integer.toString(wires));
            properties.setProperty("message", message.replace('\n', ' ').replace('\r', ' '));
            Path temporary = directory.resolve("viewer.properties.tmp");
            try (Writer out = Files.newBufferedWriter(temporary, StandardCharsets.UTF_8)) { properties.store(out, null); }
            Files.move(temporary, directory.resolve("viewer.properties"), StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
        } catch (Exception error) { error.printStackTrace(System.err); }
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 1) throw new IllegalArgumentException("Usage: LiveDigital.java SESSION_DIRECTORY");
        Path directory = Path.of(args[0]);
        SwingUtilities.invokeAndWait(() -> {
            try { new LiveDigital(directory); }
            catch (Exception error) { throw new RuntimeException(error); }
        });
    }
}
