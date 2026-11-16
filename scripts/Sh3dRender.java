// Sh3dRender - renders the 2D plan of a .sh3d file with Sweet Home 3D's own
// PlanComponent (headless), i.e. exactly what the plan view shows, incl. the
// background image, walls, rooms, doors/windows and door swings.
//
//   java -cp /usr/share/java/sweethome3d/SweetHome3D.jar scripts/Sh3dRender.java in.sh3d out.png [scale]
import com.eteks.sweethome3d.io.DefaultUserPreferences;
import com.eteks.sweethome3d.io.HomeFileRecorder;
import com.eteks.sweethome3d.model.Home;
import com.eteks.sweethome3d.swing.PlanComponent;
import java.awt.*;
import java.awt.image.BufferedImage;
import java.io.File;
import javax.imageio.ImageIO;

public class Sh3dRender {
    public static void main(String[] args) throws Exception {
        System.setProperty("java.awt.headless", "true");
        Home home = new HomeFileRecorder(0, false, null, false, true).readHome(args[0]);
        PlanComponent plan = new PlanComponent(home, new DefaultUserPreferences(), null);
        plan.setScale(args.length > 2 ? Float.parseFloat(args[2]) : 0.5f);
        Dimension d = plan.getPreferredSize();
        plan.setSize(d);
        plan.setDoubleBuffered(false);
        BufferedImage img = new BufferedImage(d.width, d.height, BufferedImage.TYPE_INT_RGB);
        Graphics2D g = img.createGraphics();
        g.setColor(Color.WHITE);
        g.fillRect(0, 0, d.width, d.height);
        plan.paint(g);
        g.dispose();
        ImageIO.write(img, "png", new File(args[1]));
        System.out.println("rendered " + d.width + "x" + d.height);
    }
}
