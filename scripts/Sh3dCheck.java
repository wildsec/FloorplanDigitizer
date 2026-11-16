// Sh3dCheck - loads a .sh3d file with Sweet Home 3D's own reader (headless) and
// prints what Sweet Home 3D understood, one line per element. Used by the tests
// as an independent oracle when Java and SweetHome3D.jar are available.
//
//   java -cp /usr/share/java/sweethome3d/SweetHome3D.jar scripts/Sh3dCheck.java file.sh3d
//
// Output lines (tab separated):
//   HOME   <wallCount> <roomCount> <levelCount> <wallHeight>
//   LEVEL  <name> <elevation> <floorThickness> <height>
//   WALL   <xStart> <yStart> <xEnd> <yEnd> <thickness> <height|-> <arcExtent|-> <atStart?> <atEnd?> <length> <midX> <midY>
//   ROOM   <name|-> <points> <area>
//   BG     <scale> <xOrigin> <yOrigin> <imageWidthPx> <imageHeightPx>
//   DW     <name> <x> <y> <width> <depth> <height> <elevation> <angle> <sashes> <modelLoadable>
import com.eteks.sweethome3d.io.HomeFileRecorder;
import com.eteks.sweethome3d.model.*;
import java.util.Locale;

public class Sh3dCheck {
    public static void main(String[] args) throws Exception {
        Locale.setDefault(Locale.ROOT);
        System.setProperty("java.awt.headless", "true");
        // compression 0, no temporary content, no preferences, prefer the Home.xml entry
        Home home = new HomeFileRecorder(0, false, null, false, true).readHome(args[0]);
        System.out.printf(Locale.ROOT, "HOME\t%d\t%d\t%d\t%.3f%n", home.getWalls().size(), home.getRooms().size(),
                home.getLevels().size(), home.getWallHeight());
        for (Level l : home.getLevels()) {
            System.out.printf(Locale.ROOT, "LEVEL\t%s\t%.3f\t%.3f\t%.3f%n", l.getName(), l.getElevation(), l.getFloorThickness(), l.getHeight());
        }
        for (Wall w : home.getWalls()) {
            float[][] p = w.getPoints();
            // the middle of the wall's left side approximates the axis middle for straight walls;
            // for arcs we print the midpoint of the left-side polyline
            float[] mid = p[(p.length / 2) / 2];
            System.out.printf(Locale.ROOT, "WALL\t%.3f\t%.3f\t%.3f\t%.3f\t%.3f\t%s\t%s\t%b\t%b\t%.3f\t%.3f\t%.3f%n",
                    w.getXStart(), w.getYStart(), w.getXEnd(), w.getYEnd(), w.getThickness(),
                    w.getHeight() == null ? "-" : String.format(Locale.ROOT, "%.3f", w.getHeight()),
                    w.getArcExtent() == null ? "-" : String.format(Locale.ROOT, "%.6f", w.getArcExtent()),
                    w.getWallAtStart() != null, w.getWallAtEnd() != null, w.getLength(), mid[0], mid[1]);
        }
        for (Room r : home.getRooms()) {
            System.out.printf(Locale.ROOT, "ROOM\t%s\t%d\t%.3f%n", r.getName() == null ? "-" : r.getName(), r.getPoints().length, r.getArea());
        }
        for (HomePieceOfFurniture f : home.getFurniture()) {
            if (f instanceof HomeDoorOrWindow) {
                HomeDoorOrWindow d = (HomeDoorOrWindow) f;
                boolean model;
                try (java.io.InputStream in = d.getModel().openStream()) { model = in.read() >= 0; }
                catch (Exception e) { model = false; }
                System.out.printf(Locale.ROOT, "DW\t%s\t%.3f\t%.3f\t%.3f\t%.3f\t%.3f\t%.3f\t%.6f\t%d\t%b%n", d.getName(),
                        d.getX(), d.getY(), d.getWidth(), d.getDepth(), d.getHeight(), d.getElevation(), d.getAngle(),
                        d.getSashes().length, model);
            }
        }
        BackgroundImage bg = home.getBackgroundImage();
        if (bg == null && !home.getLevels().isEmpty()) {
            bg = home.getLevels().get(0).getBackgroundImage();
        }
        if (bg != null) {
            java.awt.image.BufferedImage img = javax.imageio.ImageIO.read(bg.getImage().openStream());
            System.out.printf(Locale.ROOT, "BG\t%.6f\t%.3f\t%.3f\t%d\t%d%n", bg.getScale(), bg.getXOrigin(), bg.getYOrigin(),
                    img.getWidth(), img.getHeight());
        }
    }
}
