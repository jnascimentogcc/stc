package stctests.catalog;

import java.lang.annotation.Documented;
import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/**
 * Describes a step definition for the action catalog: what it does in natural language and which
 * layer of the library it belongs to. Every Cucumber step definition must carry it; the catalog
 * (catalog/actions.json) is generated from these.
 */
@Documented
@Retention(RetentionPolicy.RUNTIME)
@Target(ElementType.METHOD)
public @interface Action {

  /** Core actions are generic and shared across clients; domain actions are business-specific. */
  enum Layer {
    CORE,
    DOMAIN
  }

  /** What the action does, in one sentence a person who does not program can read. */
  String description();

  Layer layer() default Layer.DOMAIN;
}
