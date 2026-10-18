// Linux check: xvfb-run -a java --class-path "$DIGITAL_JAR" tests/TestLiveDigital.java digital_mcp_live/LiveDigital.java
import de.neemann.digital.draw.elements.*;
import de.neemann.digital.draw.graphics.Vector;
import de.neemann.digital.gui.Main;
import de.neemann.digital.gui.components.CircuitComponent;
import de.neemann.digital.gui.components.modification.ModifyInsertElement;
import java.awt.*;
import java.awt.event.*;
import java.io.*;
import java.lang.reflect.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.atomic.*;
import javax.swing.*;
import javax.tools.ToolProvider;

class TestLiveDigital {
    static Class<?> type;
    static Object field(Object object, String name) throws Exception { Field f = type.getDeclaredField(name); f.setAccessible(true); return f.get(object); }
    static void edt(Runnable run) throws Exception { SwingUtilities.invokeAndWait(run); }
    static void check(boolean value, String message) { if (!value) throw new AssertionError(message); }
    static Object create(Path directory) throws Exception {
        AtomicReference<Object> result = new AtomicReference<>(); AtomicReference<Throwable> error = new AtomicReference<>();
        edt(() -> { try { Constructor<?> c = type.getDeclaredConstructor(Path.class); c.setAccessible(true); result.set(c.newInstance(directory)); } catch(Throwable e) { error.set(e); }});
        if(error.get() != null) throw new RuntimeException(error.get()); return result.get();
    }
    static void invoke(Object o, String method) throws Exception {
        Method m=type.getDeclaredMethod(method);m.setAccessible(true);
        edt(() -> { try {m.invoke(o);}catch(Exception e){throw new RuntimeException(e);}});
    }
    static void publish(Path dir, int rev, int count, boolean complete) throws Exception {
        Circuit circuit = new Circuit();
        for(int i=0;i<count;i++) circuit.add(new VisualElement("NAnd").setPos(new Vector(100+i*1000,100)));
        if(count>1) circuit.add(new Wire(new Vector(180,120),new Vector(200,120)));
        Path snapshot=dir.resolve(String.format("revision-%06d.dig",rev));circuit.save(snapshot.toFile());
        Properties p=new Properties();p.setProperty("revision",""+rev);p.setProperty("snapshot",snapshot.getFileName().toString());p.setProperty("state",complete?"complete":"building");p.setProperty("message",complete?"PASS — finished":"Adding gates");
        if(complete){Path output=dir.resolve("final.dig");circuit.save(output.toFile());p.setProperty("output_uri",output.toUri().toString());}
        Path temp=dir.resolve("state.tmp");try(Writer out=Files.newBufferedWriter(temp)){p.store(out,null);}Files.move(temp,dir.resolve("state.properties"),StandardCopyOption.REPLACE_EXISTING,StandardCopyOption.ATOMIC_MOVE);
    }
    static Properties waitState(Path dir,String state,int rev) throws Exception {
        long until=System.nanoTime()+10_000_000_000L;Properties p=new Properties();
        do {p.clear();if(Files.exists(dir.resolve("viewer.properties")))try(Reader r=Files.newBufferedReader(dir.resolve("viewer.properties"))){p.load(r);}
            if(state.equals(p.getProperty("state")) && Integer.parseInt(p.getProperty("revision","-1"))>=rev)return p;
            Thread.sleep(30);
        }while(System.nanoTime()<until);throw new AssertionError("Expected "+state+" revision "+rev+", got "+p);
    }
    static JOptionPane optionPane(Container parent) {
        for(Component c:parent.getComponents()){if(c instanceof JOptionPane p)return p;if(c instanceof Container nested){JOptionPane p=optionPane(nested);if(p!=null)return p;}}return null;
    }
    static boolean runEnabled(MenuElement parent) {
        for(MenuElement child:parent.getSubElements()){if(child instanceof JMenuItem item && "Start of Simulation".equals(item.getText()) && item.isEnabled())return true;if(runEnabled(child))return true;}return false;
    }
    static void close(Object o) throws Exception { JFrame f=(JFrame)field(o,"frame");edt(()->f.dispatchEvent(new WindowEvent(f,WindowEvent.WINDOW_CLOSING))); }
    public static void main(String[] args) {
        try { run(args); } catch (Throwable error) { error.printStackTrace(); System.exit(1); }
    }
    static void run(String[] args)throws Exception {
        System.setProperty("java.util.prefs.userRoot", Files.createTempDirectory("live-digital-prefs-").toString());
        Locale.setDefault(Locale.ENGLISH);
        Path compiled=Files.createTempDirectory("live-digital-classes-");
        int code=ToolProvider.getSystemJavaCompiler().run(null,null,null,"-classpath",System.getProperty("java.class.path"),"-d",compiled.toString(),args[0]);check(code==0,"Source compilation");
        type=Class.forName("LiveDigital",true,new URLClassLoader(new URL[]{compiled.toUri().toURL()},TestLiveDigital.class.getClassLoader()));
        Path dir=Files.createTempDirectory("live-digital-test-");publish(dir,0,0,false);Object viewer=create(dir);
        Main frame=(Main)field(viewer,"frame");CircuitComponent canvas=(CircuitComponent)field(viewer,"canvas");
        waitState(dir,"building",0);check(frame.getGlassPane().isVisible(),"Read-only shield");check(!frame.getJMenuBar().isEnabled(),"Disabled menus");
        publish(dir,1,1,false);Properties waiting=waitState(dir,"building",1);check(waiting.getProperty("message").startsWith("Waiting for AI"),"Caught-up waiting status");
        publish(dir,2,3,false);waitState(dir,"building",2);check(canvas.contains(canvas.transform(new Vector(140,120))) && canvas.contains(canvas.transform(new Vector(2140,120))),"Auto-fit keeps distant appended gates visible before navigation");
        edt(()->canvas.translateCircuit(70,30));Point before=canvas.transform(new Vector(0,0));
        Robot robot=new Robot();robot.setAutoDelay(50);Point screen=canvas.getLocationOnScreen();robot.mouseMove(screen.x+200,screen.y+200);robot.mousePress(InputEvent.BUTTON1_DOWN_MASK);robot.mouseRelease(InputEvent.BUTTON1_DOWN_MASK);
        for(int key:new int[]{KeyEvent.VK_N,KeyEvent.VK_O}){robot.keyPress(KeyEvent.VK_CONTROL);robot.keyPress(key);robot.keyRelease(key);robot.keyRelease(KeyEvent.VK_CONTROL);}
        robot.keyPress(KeyEvent.VK_DELETE);robot.keyRelease(KeyEvent.VK_DELETE);robot.keyPress(KeyEvent.VK_F6);robot.keyRelease(KeyEvent.VK_F6);robot.waitForIdle();
        check(canvas.getCircuit().getElements().size()==3,"Keyboard and click cannot edit");check(frame.getModel()==null,"No simulation");check(Arrays.stream(Window.getWindows()).filter(Window::isVisible).count()==1,"No native shortcut dialogs");
        robot.mouseMove(screen.x+250,screen.y+220);robot.mouseWheel(-1);robot.mousePress(InputEvent.BUTTON1_DOWN_MASK);robot.mouseMove(screen.x+280,screen.y+240);robot.mouseRelease(InputEvent.BUTTON1_DOWN_MASK);robot.waitForIdle();check(!before.equals(canvas.transform(new Vector(0,0))),"Read-only pan and zoom gestures");before=canvas.transform(new Vector(0,0));
        publish(dir,3,4,false);waitState(dir,"building",3);check(before.equals(canvas.transform(new Vector(0,0))),"Append preserves viewport");
        long burstStart=System.nanoTime();publish(dir,4,500,true);Properties done=waitState(dir,"complete",4);check(System.nanoTime()-burstStart<6_000_000_000L,"Burst replay is bounded");check(canvas.getCircuit().getElements().size()==500,"Final burst contains all components");check(!frame.getGlassPane().isVisible(),"Unlocked after finish");check(canvas.isEnabled(),"Native canvas re-enabled");check(frame.getBaseFileName().equals(dir.resolve("final.dig").toFile()),"Same frame final path");check(frame.getCircuitComponent()==canvas,"Same native canvas");check(Arrays.stream(Window.getWindows()).filter(Window::isVisible).count()==1,"Same window");
        check(frame.getDefaultCloseOperation()==WindowConstants.DO_NOTHING_ON_CLOSE,"Native close policy restored");check(runEnabled(frame.getJMenuBar()),"Native Run action enabled");
        edt(()->canvas.modify(new ModifyInsertElement(new VisualElement("NAnd").setPos(new Vector(100,300)).setShapeFactory(frame.getLibrary().getShapeFactory()))));check(frame.isStateChanged(),"Native edit works after finish");
        Field saveField=Main.class.getDeclaredField("save");saveField.setAccessible(true);check(((Action)saveField.get(frame)).isEnabled(),"Native Save action enabled");
        AtomicBoolean cancelled=new AtomicBoolean();javax.swing.Timer cancel=new javax.swing.Timer(100,e->{for(Window w:Window.getWindows())if(w instanceof JDialog dialog && dialog.isVisible()){JOptionPane pane=optionPane(dialog);if(pane!=null){cancelled.set(true);pane.setValue(JOptionPane.CANCEL_OPTION);}}});
        edt(cancel::start);close(viewer);edt(cancel::stop);check(cancelled.get() && frame.isDisplayable() && frame.isStateChanged(),"Cancel close preserves unsaved editable circuit");edt(()->canvas.getUndoAction().actionPerformed(null));close(viewer);
        Path stopDir=Files.createTempDirectory("live-digital-stop-");publish(stopDir,0,0,false);Object stopped=create(stopDir);waitState(stopDir,"building",0);publish(stopDir,1,6,false);Files.writeString(stopDir.resolve("stopped.dig"),"USER DATA");invoke(stopped,"stop");waitState(stopDir,"stopped",1);check(Files.exists(stopDir.resolve("stop")),"Stop marker");check(Files.readString(stopDir.resolve("stopped.dig")).equals("USER DATA"),"Draft never overwrites");check(Files.exists(stopDir.resolve("stopped-1.dig")),"Unique full draft");check(((Main)field(stopped,"frame")).getCircuitComponent().getCircuit().getElements().size()==6,"Stop keeps latest entire snapshot");close(stopped);
        Path closeDir=Files.createTempDirectory("live-digital-close-");publish(closeDir,0,0,false);Object closed=create(closeDir);close(closed);waitState(closeDir,"closed",0);
        Path recoveryDir=Files.createTempDirectory("live-digital-recovery-");publish(recoveryDir,0,0,false);Object recovery=create(recoveryDir);waitState(recoveryDir,"building",0);publish(recoveryDir,1,3,false);waitState(recoveryDir,"building",1);
        Path malformed=recoveryDir.resolve("revision-000002.dig");Files.writeString(malformed,"<broken-circuit>");Files.writeString(recoveryDir.resolve("state.tmp"),"revision=2\nstate=building\nsnapshot=revision-000002.dig\nmessage=Invalid latest snapshot\n");Files.move(recoveryDir.resolve("state.tmp"),recoveryDir.resolve("state.properties"),StandardCopyOption.REPLACE_EXISTING,StandardCopyOption.ATOMIC_MOVE);
        waitState(recoveryDir,"error",1);invoke(recovery,"stop");Properties rescued=waitState(recoveryDir,"stopped",1);Main recoveredFrame=(Main)field(recovery,"frame");check(recoveredFrame.getCircuitComponent().getCircuit().getElements().size()==3 && !recoveredFrame.getGlassPane().isVisible(),"Stop recovers prior complete valid snapshot and unlocks");check(rescued.getProperty("message").contains("recovered previous valid snapshot"),"Recovery status explains fallback");check(Files.readString(malformed).equals("<broken-circuit>"),"Failed latest snapshot is preserved");close(recovery);
        Path bad=Files.createTempDirectory("live-digital-invalid-");Files.writeString(bad.resolve("state.properties"),"revision=0\nstate=building\nsnapshot=../outside.dig\n");boolean rejected=false;try{create(bad);}catch(RuntimeException e){rejected=true;}check(rejected && !Files.exists(bad.resolve("viewer.properties")),"Invalid initial snapshot never reports ready");
        System.out.println("PASS: Digital.jar compile; progressive/bounded snapshots; read-only keys/click; native pan/zoom; distant append auto-fit; no simulation/dialogs; preserved viewport; same-frame editable finish; Run/Save enabled; cancel unsaved close; stop latest draft/no overwrite; malformed latest recovery; close; path rejection");System.exit(0);
    }
}
